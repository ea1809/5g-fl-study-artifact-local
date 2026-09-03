import sys
from scapy.all import rdpcap
from scapy.contrib.pfcp import PFCP
from scapy.layers.inet import IP

if len(sys.argv) < 2:
    print("Usage: python3 pfcp-extract-seids.py <pcap>")
    sys.exit(1)

SMF_IP = "10.10.4.103"
UPF_IP = "10.10.4.3"

pkts = rdpcap(sys.argv[1])

max_seq = 0
seids_upf = set()
seids_smf = set()

for p in pkts:
    try:
        if PFCP not in p or IP not in p:
            continue

        pfcp = p[PFCP]
        max_seq = max(max_seq, pfcp.seq)

        if not getattr(pfcp, "IE_list", None):
            continue

        for ie in pfcp.IE_list:
            # IE type 57 = F-SEID
            if getattr(ie, "ietype", None) == 57:
                seid = getattr(ie, "seid", None)
                if seid in (None, 0):
                    continue

                if p[IP].src == UPF_IP:
                    seids_upf.add(seid)
                    print(f"[UPF->SMF] SEID={hex(seid)} msg_type={pfcp.message_type} seq={pfcp.seq}")
                elif p[IP].src == SMF_IP:
                    seids_smf.add(seid)
                    print(f"[SMF->UPF] SEID={hex(seid)} msg_type={pfcp.message_type} seq={pfcp.seq}")
                else:
                    print(f"[OTHER] {p[IP].src}->{p[IP].dst} SEID={hex(seid)} msg_type={pfcp.message_type} seq={pfcp.seq}")

    except Exception:
        pass

target_seids = seids_upf if seids_upf else seids_smf
label = "UPF" if seids_upf else "SMF fallback"

print(f"\nSEQ max observe : {max_seq}")
print(f"SEIDs UPF : {[hex(s) for s in seids_upf]}")
print(f"SEIDs SMF : {[hex(s) for s in seids_smf]}")
print(f"\nSEIDs cibles ({label}) : {[hex(s) for s in target_seids]}")
print(f"Total : {len(target_seids)}")
print(f"SEQ attaque : {max_seq + 1}")

if target_seids:
    args = " ".join([
        f"{hex(s)}:{max_seq + 1 + i}:8805"
        for i, s in enumerate(target_seids)
    ])
    print(f"\nArguments prets :\n  {args}")
