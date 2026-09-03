#!/usr/bin/env python3
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd
from tqdm import tqdm

RUN_DIR = Path(sys.argv[1])
WINDOW_S = int(sys.argv[2]) if len(sys.argv) > 2 else 5
GUARD_S = 2

EVENTS_PATH = RUN_DIR / "events.csv"
PCAP_N3 = RUN_DIR / "pcaps" / f"{RUN_DIR.name}_n3.pcap"
PCAP_N4 = RUN_DIR / "pcaps" / f"{RUN_DIR.name}_n4.pcap"

OUT_N3 = RUN_DIR / f"dataset_n3_global_w{WINDOW_S}s.csv"
OUT_N4 = RUN_DIR / f"dataset_n4_global_w{WINDOW_S}s.csv"
OUT_CROSS = RUN_DIR / f"dataset_crossplane_global_w{WINDOW_S}s.csv"

# IPs légitimes sur N3
LEGIT_N3_IPS = {"10.10.3.2", "10.10.3.3", "10.10.3.231", "10.10.3.232"}


def load_events():
    events = pd.read_csv(EVENTS_PATH)
    events["start_epoch"] = pd.to_datetime(events["start_ts"]).map(lambda x: int(x.timestamp())) + GUARD_S
    events["end_epoch"] = pd.to_datetime(events["end_ts"]).map(lambda x: int(x.timestamp())) - GUARD_S
    return events


EVENTS = load_events()


def label_for_time(t):
    rows = EVENTS[(EVENTS["start_epoch"] <= t) & (t < EVENTS["end_epoch"])]
    if rows.empty:
        return "unknown", "unknown"
    r = rows.iloc[0]
    return r["label"], r["category"]


def estimate_packet_count(pcap_path):
    try:
        out = subprocess.check_output(
            ["capinfos", "-M", "-c", str(pcap_path)],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in out.splitlines():
            if "Number of packets" in line:
                return int(line.split(":")[-1].strip().replace(",", "").replace(" ", ""))
    except Exception:
        return None
    return None


def split_outer_inner(value):
    if not value:
        return "", ""
    vals = [x for x in value.split(",") if x]
    if len(vals) == 1:
        return vals[0], ""
    return vals[0], vals[-1]


def run_tshark(pcap_path, fields, dissectors):
    cmd = [
        "tshark", "-n",
        "-r", str(pcap_path),
        "-o", "tcp.desegment_tcp_streams:FALSE",
        "-o", "tcp.analyze_sequence_numbers:FALSE",
        "-o", "ip.defragment:FALSE",
        "-T", "fields",
        "-E", "separator=|",
        "-E", "header=n",
        "-E", "occurrence=a",
        "-E", "aggregator=,",
    ]
    for d in dissectors:
        cmd += ["-d", d]
    for f in fields:
        cmd += ["-e", f]

    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def window_start(t):
    t = int(float(t))
    return t - (t % WINDOW_S)


# =============================================================================
# N3 GLOBAL
# =============================================================================

N3_FIELDS = [
    "frame.time_epoch",
    "frame.len",
    "ip.src",
    "ip.dst",
    "ip.proto",
    "udp.srcport",
    "udp.dstport",
    "tcp.srcport",
    "tcp.dstport",
    "gtp.teid",
]


def extract_n3_global():
    print(f"\n[+] Extraction N3 global/windows depuis {PCAP_N3}")
    total = estimate_packet_count(PCAP_N3)

    data = defaultdict(lambda: {
        "packet_count": 0,
        "byte_count": 0,
        "pkt_len_sum": 0,
        "pkt_len_min": None,
        "pkt_len_max": 0,
        "pkt_count_for_mean": 0,
        "udp_count": 0,
        "tcp_count": 0,
        "gtp_count": 0,
        "teids": set(),
        "outer_src_ips": set(),
        "outer_dst_ips": set(),
        "inner_src_ips": set(),
        "inner_dst_ips": set(),
        "inner_src_ports": set(),
        "inner_dst_ports": set(),
        "packets_per_teid": defaultdict(int),
        "bytes_per_teid": defaultdict(int),
        "unknown_outer_src_count": 0,
        "unknown_outer_dst_count": 0,
    })

    proc = run_tshark(PCAP_N3, N3_FIELDS, ["udp.port==2152,gtp"])
    pbar = tqdm(total=total, unit="pkt", desc="n3", dynamic_ncols=True)

    for line in proc.stdout:
        parts = line.rstrip("\n").split("|")
        parts += [""] * (len(N3_FIELDS) - len(parts))

        try:
            t = float(parts[0])
            frame_len = int(parts[1] or 0)
        except ValueError:
            continue

        w = window_start(t)
        row = data[w]

        outer_src, inner_src = split_outer_inner(parts[2])
        outer_dst, inner_dst = split_outer_inner(parts[3])
        ip_proto = parts[4]
        teid = parts[9]

        row["packet_count"] += 1
        row["byte_count"] += frame_len
        row["pkt_len_sum"] += frame_len
        row["pkt_count_for_mean"] += 1
        row["pkt_len_min"] = frame_len if row["pkt_len_min"] is None else min(row["pkt_len_min"], frame_len)
        row["pkt_len_max"] = max(row["pkt_len_max"], frame_len)

        if outer_src:
            row["outer_src_ips"].add(outer_src)
            if outer_src not in LEGIT_N3_IPS:
                row["unknown_outer_src_count"] += 1
        if outer_dst:
            row["outer_dst_ips"].add(outer_dst)
            if outer_dst not in LEGIT_N3_IPS:
                row["unknown_outer_dst_count"] += 1
        if inner_src: row["inner_src_ips"].add(inner_src)
        if inner_dst: row["inner_dst_ips"].add(inner_dst)

        if ip_proto == "17":
            row["udp_count"] += 1
            if parts[5]: row["inner_src_ports"].add(parts[5])
            if parts[6]: row["inner_dst_ports"].add(parts[6])
        elif ip_proto == "6":
            row["tcp_count"] += 1
            if parts[7]: row["inner_src_ports"].add(parts[7])
            if parts[8]: row["inner_dst_ports"].add(parts[8])

        if teid:
            row["gtp_count"] += 1
            row["teids"].add(teid)
            row["packets_per_teid"][teid] += 1
            row["bytes_per_teid"][teid] += frame_len

        pbar.update(1)

    pbar.close()
    err = proc.stderr.read()
    ret = proc.wait()
    if ret != 0:
        print(err)
        raise RuntimeError("tshark N3 failed")

    rows = []
    for w, v in data.items():
        label, category = label_for_time(w)
        per_teid = list(v["packets_per_teid"].values())
        bytes_teid = list(v["bytes_per_teid"].values())

        rows.append({
            "window_start": w,
            "window_end": w + WINDOW_S,
            "window_size_s": WINDOW_S,

            "n3_packet_count": v["packet_count"],
            "n3_byte_count": v["byte_count"],
            "n3_pps": v["packet_count"] / WINDOW_S,
            "n3_bps": (v["byte_count"] * 8) / WINDOW_S,

            "n3_mean_pkt_len": v["pkt_len_sum"] / v["pkt_count_for_mean"] if v["pkt_count_for_mean"] else 0,
            "n3_min_pkt_len": v["pkt_len_min"] or 0,
            "n3_max_pkt_len": v["pkt_len_max"],

            "n3_udp_count": v["udp_count"],
            "n3_tcp_count": v["tcp_count"],
            "n3_gtp_count": v["gtp_count"],

            "n3_unique_teids": len(v["teids"]),
            "n3_unique_outer_src_ips": len(v["outer_src_ips"]),
            "n3_unique_outer_dst_ips": len(v["outer_dst_ips"]),
            "n3_unique_inner_src_ips": len(v["inner_src_ips"]),
            "n3_unique_inner_dst_ips": len(v["inner_dst_ips"]),
            "n3_unique_inner_src_ports": len(v["inner_src_ports"]),
            "n3_unique_inner_dst_ports": len(v["inner_dst_ports"]),

            "n3_mean_packets_per_teid": sum(per_teid) / len(per_teid) if per_teid else 0,
            "n3_max_packets_per_teid": max(per_teid) if per_teid else 0,
            "n3_mean_bytes_per_teid": sum(bytes_teid) / len(bytes_teid) if bytes_teid else 0,
            "n3_max_bytes_per_teid": max(bytes_teid) if bytes_teid else 0,

            "n3_unknown_outer_src_count": v["unknown_outer_src_count"],
            "n3_unknown_outer_dst_count": v["unknown_outer_dst_count"],
            "n3_unknown_outer_src_ratio": v["unknown_outer_src_count"] / v["packet_count"] if v["packet_count"] else 0,
            "n3_unknown_outer_dst_ratio": v["unknown_outer_dst_count"] / v["packet_count"] if v["packet_count"] else 0,

            "label": label,
            "category": category,
        })

    df = pd.DataFrame(rows).sort_values("window_start")
    df.to_csv(OUT_N3, index=False)
    print(f"[+] N3 global sauvegardé : {OUT_N3} ({len(df):,} lignes)")
    return df


# =============================================================================
# N4 GLOBAL
# =============================================================================

N4_FIELDS = [
    "frame.time_epoch",
    "frame.len",
    "pfcp.msg_type",
    "pfcp.seid",
    "pfcp.seqno",
    "pfcp.f_teid.teid",
    "pfcp.apply_action.drop",
    "pfcp.apply_action.forw",
]


def extract_n4_global():
    print(f"\n[+] Extraction N4 global/windows depuis {PCAP_N4}")
    total = estimate_packet_count(PCAP_N4)

    data = defaultdict(lambda: {
        "packet_count": 0,
        "byte_count": 0,
        "msg_types": defaultdict(int),
        "seids": set(),
        "seqnos": set(),
        "f_teids": set(),
        "drop_count": 0,
        "forw_count": 0,
    })

    proc = run_tshark(PCAP_N4, N4_FIELDS, ["udp.port==8805,pfcp"])
    pbar = tqdm(total=total, unit="pkt", desc="n4", dynamic_ncols=True)

    for line in proc.stdout:
        parts = line.rstrip("\n").split("|")
        parts += [""] * (len(N4_FIELDS) - len(parts))

        try:
            t = float(parts[0])
            frame_len = int(parts[1] or 0)
        except ValueError:
            continue

        w = window_start(t)
        row = data[w]

        row["packet_count"] += 1
        row["byte_count"] += frame_len

        if parts[2]:
            for m in parts[2].split(","):
                row["msg_types"][m] += 1

        if parts[3]:
            for s in parts[3].split(","):
                row["seids"].add(s)

        if parts[4]:
            for s in parts[4].split(","):
                row["seqnos"].add(s)

        if parts[5]:
            for teid in parts[5].split(","):
                row["f_teids"].add(teid)

        if parts[6] and parts[6] != "0":
            row["drop_count"] += 1

        if parts[7] and parts[7] != "0":
            row["forw_count"] += 1

        pbar.update(1)

    pbar.close()
    err = proc.stderr.read()
    ret = proc.wait()
    if ret != 0:
        print(err)
        raise RuntimeError("tshark N4 failed")

    rows = []
    for w, v in data.items():
        label, category = label_for_time(w)
        mt = v["msg_types"]

        rows.append({
            "window_start": w,
            "window_end": w + WINDOW_S,
            "window_size_s": WINDOW_S,

            "n4_pfcp_packet_count": v["packet_count"],
            "n4_pfcp_byte_count": v["byte_count"],
            "n4_pfcp_pps": v["packet_count"] / WINDOW_S,

            "pfcp_heartbeat_req_count": mt.get("1", 0),
            "pfcp_heartbeat_resp_count": mt.get("2", 0),
            "pfcp_session_establishment_req_count": mt.get("50", 0),
            "pfcp_session_establishment_resp_count": mt.get("51", 0),
            "pfcp_session_modification_req_count": mt.get("52", 0),
            "pfcp_session_modification_resp_count": mt.get("53", 0),
            "pfcp_session_deletion_req_count": mt.get("54", 0),
            "pfcp_session_deletion_resp_count": mt.get("55", 0),

            "n4_unique_pfcp_seids": len(v["seids"]),
            "n4_unique_pfcp_seqnos": len(v["seqnos"]),
            "n4_unique_pfcp_f_teids": len(v["f_teids"]),

            "pfcp_drop_count": v["drop_count"],
            "pfcp_forw_count": v["forw_count"],

            "label": label,
            "category": category,
        })

    df = pd.DataFrame(rows).sort_values("window_start")
    df.to_csv(OUT_N4, index=False)
    print(f"[+] N4 global sauvegardé : {OUT_N4} ({len(df):,} lignes)")
    return df


# =============================================================================
# CROSS GLOBAL
# =============================================================================

def build_crossplane_global(df_n3, df_n4):
    print("\n[+] Construction cross-plane global N3+N4")

    n3 = df_n3.drop(columns=["label", "category"], errors="ignore")
    n4 = df_n4.drop(columns=["label", "category"], errors="ignore")

    all_windows = pd.DataFrame({
        "window_start": sorted(set(n3["window_start"]).union(set(n4["window_start"])))
    })
    all_windows["window_end"] = all_windows["window_start"] + WINDOW_S
    all_windows["window_size_s"] = WINDOW_S

    df = all_windows.merge(n3, on=["window_start", "window_end", "window_size_s"], how="left")
    df = df.merge(n4, on=["window_start", "window_end", "window_size_s"], how="left")

    feature_cols = [c for c in df.columns if c not in ["window_start", "window_end", "window_size_s"]]
    df[feature_cols] = df[feature_cols].fillna(0)

    labels = df["window_start"].apply(lambda w: label_for_time(int(w)))
    df["label"] = labels.apply(lambda x: x[0])
    df["category"] = labels.apply(lambda x: x[1])

    df["gtp_packets_per_pfcp_packet"] = df["n3_gtp_count"] / (df["n4_pfcp_packet_count"] + 1)
    df["gtp_bytes_per_pfcp_packet"] = df["n3_byte_count"] / (df["n4_pfcp_packet_count"] + 1)
    df["teids_per_pfcp_session"] = df["n3_unique_teids"] / (df["n4_unique_pfcp_seids"] + 1)

    df["pfcp_deletion_present"] = (df["pfcp_session_deletion_req_count"] > 0).astype(int)
    df["pfcp_modification_present"] = (df["pfcp_session_modification_req_count"] > 0).astype(int)
    df["pfcp_establishment_present"] = (df["pfcp_session_establishment_req_count"] > 0).astype(int)
    df["pfcp_drop_present"] = (df["pfcp_drop_count"] > 0).astype(int)

    df.to_csv(OUT_CROSS, index=False)
    print(f"[+] Cross-plane global sauvegardé : {OUT_CROSS} ({len(df):,} lignes)")
    return df


def main():
    print(f"[+] RUN_DIR={RUN_DIR}")
    print(f"[+] WINDOW={WINDOW_S}s")

    df_n3 = extract_n3_global()
    df_n4 = extract_n4_global()
    df_cross = build_crossplane_global(df_n3, df_n4)

    print("\n[+] Labels N3 global")
    print(df_n3["label"].value_counts())

    print("\n[+] Labels N4 global")
    print(df_n4["label"].value_counts())

    print("\n[+] Labels Cross-plane global")
    print(df_cross["label"].value_counts())


if __name__ == "__main__":
    main()