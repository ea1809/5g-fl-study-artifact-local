import sys
from scapy.all import rdpcap
from scapy.contrib.gtp import GTP_U_Header
from scapy.layers.inet import IP

if len(sys.argv) < 2:
    print("Usage: python3 gtp-extract-mapping.py <pcap>")
    sys.exit(1)

pkts = rdpcap(sys.argv[1])
mapping = {}

for p in pkts:
    try:
        if GTP_U_Header in p and IP in p[GTP_U_Header]:
            teid = p[GTP_U_Header].teid
            inner_ip = p[GTP_U_Header][IP].src
            if teid and inner_ip.startswith("10.46."):
                mapping[teid] = inner_ip
    except:
        pass

print(f"Mapping TEID->UE_IP extrait : {len(mapping)} entrees")
for t, ip in mapping.items():
    print(f"  {hex(t)} -> {ip}")

print(f"\nArguments prets :")
print("  " + " ".join([f"{hex(t)}:{ip}" for t, ip in mapping.items()]))
