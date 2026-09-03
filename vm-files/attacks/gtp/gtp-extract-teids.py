import sys
import subprocess

if len(sys.argv) < 2:
    print("Usage: python3 gtp-extract-teids.py <pcap>")
    sys.exit(1)

pcap = sys.argv[1]

result = subprocess.run(
    ["tshark", "-r", pcap, "-V"],
    capture_output=True, text=True
)

teids = set()
for line in result.stdout.split('\n'):
    # Ligne du type : F-TEID : TEID: 0x00000cf6, IPv4 10.10.3.2
    if "F-TEID : TEID:" in line and "10.10.3.2" in line:
        try:
            teid_str = line.strip().split("TEID:")[1].strip().split(",")[0].strip()
            teid = int(teid_str, 16)
            if teid != 0:
                teids.add(teid)
        except:
            pass

print(f"TEIDs UPF extraits : {[hex(t) for t in teids]}")
print(f"Total : {len(teids)}")
if teids:
    print(f"\nArguments prets :\n  {' '.join([hex(t) for t in teids])}")
