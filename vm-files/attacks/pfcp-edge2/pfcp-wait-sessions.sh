#!/bin/bash
EXPECTED=${1:-4}
NAMESPACE="open5gs-edge"
DEPLOY="open5gs-upf2"

echo "[*] Surveillance en live des sessions UPF (cible: $EXPECTED)..."
echo "[*] Lance le restart des UEs maintenant."
echo ""

kubectl logs -f --since=1s deployment/$DEPLOY -n $NAMESPACE 2>/dev/null | \
while read line; do
    if echo "$line" | grep -qi "Number of UPF-Sessions is now"; then
        COUNT=$(echo "$line" | grep -oP 'now \K[0-9]+')
        echo "    [$(date +%H:%M:%S)] Sessions actives : $COUNT / $EXPECTED"
        if [ -n "$COUNT" ] && [ "$COUNT" -ge "$EXPECTED" ]; then
            echo ""
            echo "[+] $EXPECTED sessions confirmees — Ctrl+C Terminal 1 maintenant !"
            break
        fi
    fi
done
