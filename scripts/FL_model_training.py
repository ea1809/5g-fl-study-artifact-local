"""
FL ROBUST MULTI-SEED EXPERIMENTS
================================
This is the expanded version containing:
  - five training seeds;
  - FedProx mu sensitivity;
  - 30/150-round validation;
  - validation-only configuration selection;
  - parameter-matched unified-versus-fusion comparison.

FL Experiments v3 — corrected protocol.
Fixes applied:
  - _local_update: bs=32 as keyword default
  - tau fixed at 1.0 everywhere (consistent with Zhang et al.)
  - N3/N4 models use same FL method as best_fl (train_selected_fl)
  - Local baseline epochs = FL_ROUNDS * FL_EPOCHS
  - Fig 3 label: "Gap to centralized training"
"""

# %%
from pathlib import Path
import os

ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "output"

os.chdir(OUTPUT_DIR)
print("Répertoire :", os.getcwd())

# %%
import numpy as np
import pandas as pd
import copy
import torch
import torch.nn as nn
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
from sklearn.preprocessing import LabelEncoder
from sklearn.metrics import f1_score
from sklearn.model_selection import train_test_split
import warnings
warnings.filterwarnings("ignore")

# %%
SEED = 42
torch.manual_seed(SEED)
np.random.seed(SEED)

HIJACK_COLS = ["n3_gtp_unique_dst_ips", "n3_gtp_unknown_dst_count", "n3_gtp_unknown_dst_ratio"]
DROP_COLS   = ["label", "category", "window_start", "window_end", "window_size_s"]
SHORT = {
    "pfcp_flood": "pfcp_flood", "pfcp_session_del": "pfcp_del",
    "pfcp_session_mod": "pfcp_mod", "gtp_flood": "gtp_flood",
    "udp_flood_low": "udp_low", "udp_flood_medium": "udp_med",
    "udp_flood_high": "udp_high", "udp_flood_max": "udp_max",
}

FL_ROUNDS  = 30
FL_EPOCHS  = 3
FL_LR      = 1e-3
FL_TAU     = 1.0   # fixed — consistent with Zhang et al.
FL_MU      = 0.05  # FedProx
LOCAL_BASELINE_EPOCHS = FL_ROUNDS * FL_EPOCHS  # 90 — same budget as FL

# %%
# ============================================================
# 1. Load & split at DataFrame level
# ============================================================
df_e1 = pd.read_csv("run_fl_edge1/dataset_crossplane_global_w5s.csv")
df_e1 = df_e1[df_e1["label"] != "unknown"].drop(columns=HIJACK_COLS, errors="ignore")
df_e2 = pd.read_csv("run_fl_edge2/dataset_crossplane_global_w5s.csv")
df_e2 = df_e2[df_e2["label"] != "unknown"].drop(columns=HIJACK_COLS, errors="ignore")
df_e1 = df_e1[df_e1["label"].notna()].copy()
df_e2 = df_e2[df_e2["label"].notna()].copy()

df_e1["label"] = df_e1["label"].replace({
    "ue_http_intensive": "normal"
})

df_e2["label"] = df_e2["label"].replace({
    "ue_http_intensive": "normal"
})

feature_cols = [c for c in df_e1.columns if c not in DROP_COLS]
N3_COLS = [c for c in feature_cols if c.startswith("n3_")]
N4_COLS = [c for c in feature_cols if c.startswith("n4_") or c.startswith("pfcp_")]

# %%
def df_split(df, val_size=0.15, test_size=0.20, seed=SEED):
    tr_val, te = train_test_split(df, test_size=test_size, random_state=seed, stratify=df["label"])
    tr, va     = train_test_split(tr_val, test_size=val_size/(1-test_size), random_state=seed, stratify=tr_val["label"])
    return tr.reset_index(drop=True), va.reset_index(drop=True), te.reset_index(drop=True)

train_e1, val_e1, test_e1 = df_split(df_e1)
train_e2, val_e2, test_e2 = df_split(df_e2)

train_global = pd.concat([train_e1, train_e2], ignore_index=True)
val_global   = pd.concat([val_e1,   val_e2],   ignore_index=True)
test_global  = pd.concat([test_e1,  test_e2],  ignore_index=True)

all_labels = sorted(set(df_e1["label"]) | set(df_e2["label"]))
le = LabelEncoder(); le.fit(all_labels)
n_classes = len(all_labels)

ATK_E1  = sorted([l for l in df_e1["label"].unique() if l != "normal"])
ATK_E2  = sorted([l for l in df_e2["label"].unique() if l != "normal"])
ATK_ALL = sorted([l for l in all_labels if l != "normal"])
UNSEEN_ON_E2 = [a for a in ATK_ALL if a not in ATK_E2]
UNSEEN_ON_E1 = [a for a in ATK_ALL if a not in ATK_E1]

print(f"Edge1 train/val/test: {len(train_e1)}/{len(val_e1)}/{len(test_e1)}")
print(f"Edge2 train/val/test: {len(train_e2)}/{len(val_e2)}/{len(test_e2)}")
print(f"Unseen on Edge1: {UNSEEN_ON_E1}")
print(f"Unseen on Edge2: {UNSEEN_ON_E2}")

# %%
# ============================================================
# 2. Normalization — train only, Chan formula
# ============================================================
def chan_combine(stats):
    n_tot = sum(n for n, _, _ in stats)
    mu    = sum(n*m for n,m,_ in stats) / n_tot
    sigma = np.sqrt(sum(n*(v+(m-mu)**2) for n,m,v in stats) / n_tot) + 1e-8
    return mu, sigma

def fit_stats(df, cols):
    v = df[cols].values.astype(float)
    return v.mean(0), v.var(0)

def transform(df, cols, mean, std):
    return ((df[cols].values.astype(float) - mean) / std).astype(np.float32)

mu1, v1 = fit_stats(train_e1, feature_cols)
mu2, v2 = fit_stats(train_e2, feature_cols)
mean_fed, std_fed = chan_combine([(len(train_e1), mu1, v1), (len(train_e2), mu2, v2)])

mean_e1, std_e1 = mu1, np.sqrt(v1) + 1e-8
mean_e2, std_e2 = mu2, np.sqrt(v2) + 1e-8

mu1n3, v1n3 = fit_stats(train_e1, N3_COLS)
mu2n3, v2n3 = fit_stats(train_e2, N3_COLS)
mean_n3, std_n3 = chan_combine([(len(train_e1), mu1n3, v1n3), (len(train_e2), mu2n3, v2n3)])

mu1n4, v1n4 = fit_stats(train_e1, N4_COLS)
mu2n4, v2n4 = fit_stats(train_e2, N4_COLS)
mean_n4, std_n4 = chan_combine([(len(train_e1), mu1n4, v1n4), (len(train_e2), mu2n4, v2n4)])

def make_sets(tr, va, te):
    return {
        "X_tr": transform(tr, feature_cols, mean_fed, std_fed),
        "X_va": transform(va, feature_cols, mean_fed, std_fed),
        "X_te": transform(te, feature_cols, mean_fed, std_fed),
        "X_tr_n3": transform(tr, N3_COLS, mean_n3, std_n3),
        "X_te_n3": transform(te, N3_COLS, mean_n3, std_n3),
        "X_tr_n4": transform(tr, N4_COLS, mean_n4, std_n4),
        "X_te_n4": transform(te, N4_COLS, mean_n4, std_n4),
        "y_tr": le.transform(tr["label"].values),
        "y_va": le.transform(va["label"].values),
        "y_te": le.transform(te["label"].values),
        "label_tr": tr["label"].values,
        "label_va": va["label"].values,
        "label_te": te["label"].values,
    }

S1 = make_sets(train_e1, val_e1, test_e1)
S2 = make_sets(train_e2, val_e2, test_e2)
Sg = make_sets(train_global, val_global, test_global)

S1_loc = {
    "X_tr": transform(train_e1, feature_cols, mean_e1, std_e1),
    "X_te": transform(test_e1,  feature_cols, mean_e1, std_e1),
    "y_tr": le.transform(train_e1["label"].values),
    "label_te": test_e1["label"].values,
}
S2_loc = {
    "X_tr": transform(train_e2, feature_cols, mean_e2, std_e2),
    "X_te": transform(test_e2,  feature_cols, mean_e2, std_e2),
    "y_tr": le.transform(train_e2["label"].values),
    "label_te": test_e2["label"].values,
}

# %%
# ============================================================
# 3. Model & utilities
# ============================================================
class MLP(nn.Module):
    def __init__(self, d, n, h1=32, h2=16):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(d, h1), nn.ReLU(),
            nn.Linear(h1, h2), nn.ReLU(),
            nn.Linear(h2, n))
    def forward(self, x): return self.net(x)

def class_weights(y, n):
    c = np.bincount(y, minlength=n).astype(float); c[c==0] = 1.0
    w = 1.0/c; w = w/w.sum()*n
    return torch.tensor(w, dtype=torch.float32)

def train_mlp(X, y, n, epochs=LOCAL_BASELINE_EPOCHS, lr=1e-3, bs=32):
    torch.manual_seed(SEED)
    m = MLP(X.shape[1], n); cw = class_weights(y, n)
    opt = torch.optim.Adam(m.parameters(), lr=lr)
    Xt = torch.tensor(X, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.long)
    m.train()
    for _ in range(epochs):
        perm = torch.randperm(len(Xt))
        for i in range(0, len(Xt), bs):
            idx = perm[i:i+bs]; opt.zero_grad()
            nn.CrossEntropyLoss(weight=cw)(m(Xt[idx]), yt[idx]).backward(); opt.step()
    return m

def predict(m, X):
    m.eval()
    with torch.no_grad():
        p = m(torch.tensor(X, dtype=torch.float32)).argmax(1).numpy()
    return le.inverse_transform(p)

def predict_fusion(ma, mb, Xa, Xb):
    ma.eval(); mb.eval()
    with torch.no_grad():
        p = (ma(torch.tensor(Xa, dtype=torch.float32)) +
             mb(torch.tensor(Xb, dtype=torch.float32))).argmax(1).numpy()
    return le.inverse_transform(p)

def recall_per_attack(y_true, y_pred, attacks):
    yt, yp = np.array(y_true), np.array(y_pred)
    return {cls: float(((yp==cls)&(yt==cls)).sum()/max((yt==cls).sum(),1))
            for cls in attacks}

def macro_f1(y_true, y_pred):
    return f1_score(y_true, y_pred, average="macro", zero_division=0)

# %%
# ============================================================
# 4. FL algorithms
# ============================================================
def _local_update(gm, Xt, yt, cw, epochs, lr, bs=32, mu=0.0, gp=None):
    m = copy.deepcopy(gm)
    opt = torch.optim.Adam(m.parameters(), lr=lr)
    for _ in range(epochs):
        perm = torch.randperm(len(Xt))
        for i in range(0, len(Xt), bs):
            idx = perm[i:i+bs]; opt.zero_grad()
            loss = nn.CrossEntropyLoss(weight=cw)(m(Xt[idx]), yt[idx])
            if mu > 0 and gp is not None:
                loss += mu/2 * sum((p-gp[k]).norm()**2 for k,p in m.named_parameters())
            loss.backward(); opt.step()
    return m

def _aggregate(gm, clients):
    tot = sum(n for _,n in clients)
    gm.load_state_dict({k: sum(c.state_dict()[k]*n/tot for c,n in clients)
                        for k in gm.state_dict()})
    return gm

def run_fedavg(
    X1, y1, X2, y2, hidden=(32, 16), rounds=FL_ROUNDS
):
    torch.manual_seed(SEED)
    gm = MLP(X1.shape[1], n_classes, *hidden)
    Xt1=torch.tensor(X1,dtype=torch.float32); yt1=torch.tensor(y1,dtype=torch.long)
    Xt2=torch.tensor(X2,dtype=torch.float32); yt2=torch.tensor(y2,dtype=torch.long)
    cw1=class_weights(y1,n_classes); cw2=class_weights(y2,n_classes)
    for _ in range(rounds):
        m1=_local_update(gm,Xt1,yt1,cw1,FL_EPOCHS,FL_LR)
        m2=_local_update(gm,Xt2,yt2,cw2,FL_EPOCHS,FL_LR)
        gm=_aggregate(gm,[(m1,len(Xt1)),(m2,len(Xt2))])
    return gm

def run_fedprox(
    X1, y1, X2, y2, mu=FL_MU,
    hidden=(32, 16), rounds=FL_ROUNDS
):
    torch.manual_seed(SEED)
    gm = MLP(X1.shape[1], n_classes, *hidden)
    Xt1=torch.tensor(X1,dtype=torch.float32); yt1=torch.tensor(y1,dtype=torch.long)
    Xt2=torch.tensor(X2,dtype=torch.float32); yt2=torch.tensor(y2,dtype=torch.long)
    cw1=class_weights(y1,n_classes); cw2=class_weights(y2,n_classes)
    for _ in range(rounds):
        gp={k:v.detach().clone() for k,v in gm.named_parameters()}
        m1=_local_update(gm,Xt1,yt1,cw1,FL_EPOCHS,FL_LR,mu=mu,gp=gp)
        m2=_local_update(gm,Xt2,yt2,cw2,FL_EPOCHS,FL_LR,mu=mu,gp=gp)
        gm=_aggregate(gm,[(m1,len(Xt1)),(m2,len(Xt2))])
    return gm

def run_fedlc(
    X1, y1, X2, y2, tau=FL_TAU,
    hidden=(32, 16), rounds=FL_ROUNDS
):
    cw1 = class_weights(y1, n_classes)
    cw2 = class_weights(y2, n_classes)
    torch.manual_seed(SEED)
    gm = MLP(X1.shape[1], n_classes, *hidden)
    Xt1=torch.tensor(X1,dtype=torch.float32); yt1=torch.tensor(y1,dtype=torch.long)
    Xt2=torch.tensor(X2,dtype=torch.float32); yt2=torch.tensor(y2,dtype=torch.long)
    def lc_loss(logits, tgts, yt_all, weights):
        counts = torch.bincount(
            yt_all,
            minlength=n_classes
        ).float().clamp(min=1)

        calibrated_logits = (
            logits - tau * counts.pow(-0.25).unsqueeze(0)
        )

        return nn.CrossEntropyLoss(weight=weights)(
            calibrated_logits,
            tgts
        )
    for _ in range(rounds):
        clients=[]
        for Xt, yt, cw in [
            (Xt1, yt1, cw1),
            (Xt2, yt2, cw2),
        ]:
            m=copy.deepcopy(gm)
            opt=torch.optim.Adam(m.parameters(),lr=FL_LR)
            for _ in range(FL_EPOCHS):
                perm=torch.randperm(len(Xt))
                for i in range(0,len(Xt),32):
                    idx=perm[i:i+32]; opt.zero_grad()
                    lc_loss(m(Xt[idx]), yt[idx], yt, cw).backward(); opt.step()
            clients.append((m,len(Xt)))
        gm=_aggregate(gm,clients)
    return gm

def train_selected_fl(
    method, X1, y1, X2, y2,
    hidden=(32, 16), rounds=FL_ROUNDS,
    mu=FL_MU, tau=FL_TAU,
):
    if method == "FedAvg":
        return run_fedavg(
            X1, y1, X2, y2,
            hidden=hidden, rounds=rounds
        )
    if method == "FedProx":
        return run_fedprox(
            X1, y1, X2, y2, mu=mu,
            hidden=hidden, rounds=rounds
        )
    if method == "FedLC":
        return run_fedlc(
            X1, y1, X2, y2, tau=tau,
            hidden=hidden, rounds=rounds
        )
    raise ValueError(f"Unknown FL method: {method}")

# %%
# ============================================================
# 5. Train models
# ============================================================
print("\nTraining local Edge1 (epochs=%d)..." % LOCAL_BASELINE_EPOCHS)
m_loc1 = train_mlp(S1_loc["X_tr"], S1_loc["y_tr"], n_classes)

print("Training local Edge2 (epochs=%d)..." % LOCAL_BASELINE_EPOCHS)
m_loc2 = train_mlp(S2_loc["X_tr"], S2_loc["y_tr"], n_classes)

print("Training FedAvg...")
m_fedavg  = run_fedavg(S1["X_tr"], S1["y_tr"], S2["X_tr"], S2["y_tr"])

print("Training FedProx (mu=%.2f)..." % FL_MU)
m_fedprox = run_fedprox(S1["X_tr"], S1["y_tr"], S2["X_tr"], S2["y_tr"])

print("Training FedLC (tau=%.1f)..." % FL_TAU)
m_fedlc   = run_fedlc(S1["X_tr"], S1["y_tr"], S2["X_tr"], S2["y_tr"])

# %%
# ============================================================
# 6. Select the FL method on validation, then report on test
# ============================================================
val_scores = {}
for name, model in [("FedAvg", m_fedavg), ("FedProx", m_fedprox), ("FedLC", m_fedlc)]:
    preds = predict(model, Sg["X_va"])
    val_scores[name] = macro_f1(Sg["label_va"], preds)
    print(f"  Validation macro F1 — {name}: {val_scores[name]:.3f}")

fl_test_scores = {}
for name, model in [
    ("FedAvg", m_fedavg),
    ("FedProx", m_fedprox),
    ("FedLC", m_fedlc),
]:
    predictions = predict(model, Sg["X_te"])
    fl_test_scores[name] = macro_f1(Sg["label_te"], predictions)
    print(f"  Test macro F1 — {name}: {fl_test_scores[name]:.3f}")

# Select the method without using the final test set.
best_fl_name = max(val_scores, key=val_scores.get)
best_fl_model = {"FedAvg": m_fedavg, "FedProx": m_fedprox, "FedLC": m_fedlc}[best_fl_name]
print(
    f"\nFL method selected on validation: "
    f"{best_fl_name} ({val_scores[best_fl_name]:.3f})"
)

# Train N3/N4 models using SAME method as best FL
print(f"\nTraining N3-only FL ({best_fl_name})...")
m_n3 = train_selected_fl(best_fl_name, S1["X_tr_n3"], S1["y_tr"], S2["X_tr_n3"], S2["y_tr"])

print(f"Training N4-only FL ({best_fl_name})...")
m_n4 = train_selected_fl(best_fl_name, S1["X_tr_n4"], S1["y_tr"], S2["X_tr_n4"], S2["y_tr"])

# Capacity-controlled models.
# Compact fusion: 2248 parameters, versus 2249 for the base unified model.
print(f"Training compact N3-only FL ({best_fl_name}, 26->14)...")
m_n3_compact = train_selected_fl(
    best_fl_name,
    S1["X_tr_n3"], S1["y_tr"],
    S2["X_tr_n3"], S2["y_tr"],
    hidden=(26, 14),
)

print(f"Training compact N4-only FL ({best_fl_name}, 26->14)...")
m_n4_compact = train_selected_fl(
    best_fl_name,
    S1["X_tr_n4"], S1["y_tr"],
    S2["X_tr_n4"], S2["y_tr"],
    hidden=(26, 14),
)

# Enlarged unified model: 2867 parameters, versus 2866 for full fusion.
print(f"Training enlarged unified FL ({best_fl_name}, 42->16)...")
m_unified_large = train_selected_fl(
    best_fl_name,
    S1["X_tr"], S1["y_tr"],
    S2["X_tr"], S2["y_tr"],
    hidden=(42, 16),
)

# %%
# ============================================================
# FIG 1 — Local Edge 1, Local Edge 2 and FedAvg per attack
# All three models are evaluated on the same pooled test windows.
# ============================================================
# Display shared attacks first, followed by attacks local to each edge.
attacks_common = sorted(set(ATK_E1) & set(ATK_E2))
attacks_e1_only = sorted(set(ATK_E1) - set(ATK_E2))
attacks_e2_only = sorted(set(ATK_E2) - set(ATK_E1))
attacks_ordered = attacks_common + attacks_e1_only + attacks_e2_only

# Evaluate all three models on the same raw pooled test windows.
# Each local model retains the normalization learned at its own edge.
y_fig1 = test_global["label"].values
X_fig1_e1 = transform(test_global, feature_cols, mean_e1, std_e1)
X_fig1_e2 = transform(test_global, feature_cols, mean_e2, std_e2)
X_fig1_fl = transform(test_global, feature_cols, mean_fed, std_fed)

rec_e1 = recall_per_attack(
    y_fig1, predict(m_loc1, X_fig1_e1), attacks_ordered
)
rec_e2 = recall_per_attack(
    y_fig1, predict(m_loc2, X_fig1_e2), attacks_ordered
)
rec_fl = recall_per_attack(
    y_fig1, predict(m_fedavg, X_fig1_fl), attacks_ordered
)

fig, ax = plt.subplots(figsize=(12, 5))
x = np.arange(len(attacks_ordered))
width = 0.24

ax.bar(
    x - width, [rec_e1[a] for a in attacks_ordered], width,
    label="Local Edge 1", color="#4C72B0"
)
ax.bar(
    x, [rec_e2[a] for a in attacks_ordered], width,
    label="Local Edge 2", color="#55A868"
)
bars_fl = ax.bar(
    x + width, [rec_fl[a] for a in attacks_ordered], width,
    label="FedAvg", color="#DD8452"
)

# Red indicates that FedAvg outperforms both independently trained models.
for i, attack in enumerate(attacks_ordered):
    if rec_fl[attack] > max(rec_e1[attack], rec_e2[attack]):
        bars_fl[i].set_color("#C44E52")
        bars_fl[i].set_edgecolor("#8B0000")
        bars_fl[i].set_linewidth(1.2)

group_specs = [
    ("Shared attacks", attacks_common, "#EAF2F8"),
    ("Edge 1 only", attacks_e1_only, "#FDF2E9"),
    ("Edge 2 only", attacks_e2_only, "#EAF7EE"),
]
start = 0
for group_label, group_attacks, background in group_specs:
    if not group_attacks:
        continue
    end = start + len(group_attacks)
    ax.axvspan(
        start - 0.5, end - 0.5,
        color=background, alpha=0.55, zorder=0
    )
    ax.text(
        (start + end - 1) / 2, 1.035, group_label,
        ha="center", va="bottom", fontsize=9, fontweight="bold"
    )
    if end < len(attacks_ordered):
        ax.axvline(
            end - 0.5, color="0.55",
            linestyle="--", linewidth=0.9
        )
    start = end

ax.set_xticks(x)
ax.set_xticklabels(
    [SHORT.get(a, a) for a in attacks_ordered],
    rotation=30, ha="right"
)
ax.set_ylim(0, 1.12)
ax.set_ylabel("Recall")
ax.set_title("Local and federated per-attack classification")
ax.grid(axis="y", alpha=0.3)
ax.legend(
    handles=[
        Patch(facecolor="#4C72B0", label="Local Edge 1"),
        Patch(facecolor="#55A868", label="Local Edge 2"),
        Patch(facecolor="#DD8452", label="FedAvg"),
        Patch(
            facecolor="#C44E52", edgecolor="#8B0000",
            label="FedAvg better than both local models"
        ),
    ],
    ncol=2, loc="upper left", fontsize=8
)

plt.tight_layout()
plt.savefig("fig1_local_vs_fedavg.pdf", bbox_inches="tight")
plt.savefig("fig1_local_vs_fedavg.png", dpi=300, bbox_inches="tight")
plt.show(); print("Fig 1 saved.")

# %%
# ============================================================
# FIG 3 — Unified N3+N4 vs separate N3/N4 logit fusion
#          (global mean performance, same FL method)
# ============================================================
pred_unified = predict(best_fl_model, Sg["X_te"])
pred_n3 = predict(m_n3, Sg["X_te_n3"])
pred_n4 = predict(m_n4, Sg["X_te_n4"])
pred_fusion = predict_fusion(
    m_n3,
    m_n4,
    Sg["X_te_n3"],
    Sg["X_te_n4"],
)
pred_fusion_compact = predict_fusion(
    m_n3_compact,
    m_n4_compact,
    Sg["X_te_n3"],
    Sg["X_te_n4"],
)
pred_unified_large = predict(m_unified_large, Sg["X_te"])

f1_unified = macro_f1(Sg["label_te"], pred_unified)
f1_n3 = macro_f1(Sg["label_te"], pred_n3)
f1_n4 = macro_f1(Sg["label_te"], pred_n4)
f1_fusion = macro_f1(Sg["label_te"], pred_fusion)
f1_fusion_compact = macro_f1(
    Sg["label_te"], pred_fusion_compact
)
f1_unified_large = macro_f1(
    Sg["label_te"], pred_unified_large
)

# The unified score must be identical to the score already reported
# for the selected FL model in Figure 2.
expected_unified_f1 = fl_test_scores[best_fl_name]
if not np.isclose(f1_unified, expected_unified_f1, atol=1e-12):
    raise RuntimeError(
        "Inconsistent unified-model evaluation: "
        f"Figure 2 reports {expected_unified_f1:.6f}, "
        f"whereas Figure 3 reports {f1_unified:.6f}."
    )

def parameter_count(model):
    return sum(parameter.numel() for parameter in model.parameters())

with torch.no_grad():
    logits_n3 = m_n3(
        torch.tensor(Sg["X_te_n3"], dtype=torch.float32)
    )
    logits_n4 = m_n4(
        torch.tensor(Sg["X_te_n4"], dtype=torch.float32)
    )

print("\nCross-plane diagnostic on the common test set")
print(f"  Selected FL method : {best_fl_name}")
print(f"  N3 only macro F1   : {f1_n3:.3f}")
print(f"  N4 only macro F1   : {f1_n4:.3f}")
print(f"  Logit fusion F1    : {f1_fusion:.3f}")
print(f"  Unified N3+N4 F1   : {f1_unified:.3f}")
print(f"  Unified parameters : {parameter_count(best_fl_model)}")
print(
    "  Separate parameters: "
    f"{parameter_count(m_n3) + parameter_count(m_n4)} "
    f"({parameter_count(m_n3)} + {parameter_count(m_n4)})"
)
print(
    "  Mean |logit|      : "
    f"N3={logits_n3.abs().mean().item():.3f}, "
    f"N4={logits_n4.abs().mean().item():.3f}"
)

print("\nCapacity-controlled comparison")
print(
    "  Low budget — compact fusion: "
    f"F1={f1_fusion_compact:.3f}, parameters="
    f"{parameter_count(m_n3_compact) + parameter_count(m_n4_compact)}"
)
print(
    "  Low budget — unified model : "
    f"F1={f1_unified:.3f}, "
    f"parameters={parameter_count(best_fl_model)}"
)
print(
    "  High budget — full fusion  : "
    f"F1={f1_fusion:.3f}, parameters="
    f"{parameter_count(m_n3) + parameter_count(m_n4)}"
)
print(
    "  High budget — unified model: "
    f"F1={f1_unified_large:.3f}, "
    f"parameters={parameter_count(m_unified_large)}"
)

fig, ax = plt.subplots(figsize=(7.2, 4.8))
budget_names = ["≈2.25k parameters", "≈2.87k parameters"]
fusion_values = [f1_fusion_compact, f1_fusion]
unified_values = [f1_unified, f1_unified_large]
x = np.arange(len(budget_names))
width = 0.32

bars_fusion = ax.bar(
    x - width / 2, fusion_values, width,
    label="Separate N3/N4 logit fusion",
    color="#8172B3",
)
bars_unified = ax.bar(
    x + width / 2, unified_values, width,
    label="Unified N3+N4 model",
    color="#4C72B0",
)

for bars, values in [
    (bars_fusion, fusion_values),
    (bars_unified, unified_values),
]:
    for bar, value in zip(bars, values):
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            value + 0.015,
            f"{value:.3f}",
            ha="center",
            va="bottom",
            fontweight="bold",
        )

ax.set_ylim(0, 1.08)
ax.set_ylabel("Macro F1")
ax.set_xticks(x)
ax.set_xticklabels(budget_names)
ax.set_title(
    f"Capacity-controlled cross-plane comparison ({best_fl_name})"
)
ax.grid(axis="y", alpha=0.3)
ax.legend(fontsize=8)

plt.tight_layout()
plt.savefig("fig3_n3n4_unified_vs_fusion.pdf", bbox_inches="tight")
plt.savefig("fig3_n3n4_unified_vs_fusion.png", dpi=300, bbox_inches="tight")
plt.show(); print("Fig 3 saved.")

# %%
# ============================================================
# ROBUSTNESS STUDY
# Multiple training seeds, FedProx mu values, and round counts.
# Hyperparameters are selected from mean validation F1 only.
# The held-out test set is used once for final reporting.
# ============================================================
ROBUST_SEEDS = [11, 22, 33, 44, 55]
MU_GRID = [0.001, 0.01, 0.05, 0.1, 0.5, 1.0]
ROUND_GRID = [30, 150]

candidate_configs = [
    {"name": "FedAvg", "method": "FedAvg", "mu": None},
    {"name": "FedLC", "method": "FedLC", "mu": None},
] + [
    {
        "name": f"FedProx(mu={mu:g})",
        "method": "FedProx",
        "mu": mu,
    }
    for mu in MU_GRID
]

robust_rows = []
unified_models = {}

print("\nStarting robustness study...")
for rounds in ROUND_GRID:
    for seed in ROBUST_SEEDS:
        # Keep the train/validation/test split fixed and vary only
        # initialization and minibatch order.
        SEED = seed
        torch.manual_seed(seed)
        np.random.seed(seed)

        for config in candidate_configs:
            print(
                f"  seed={seed}, rounds={rounds}, "
                f"configuration={config['name']}"
            )
            model = train_selected_fl(
                config["method"],
                S1["X_tr"], S1["y_tr"],
                S2["X_tr"], S2["y_tr"],
                hidden=(32, 16),
                rounds=rounds,
                mu=(
                    config["mu"]
                    if config["mu"] is not None
                    else FL_MU
                ),
                tau=FL_TAU,
            )

            validation_f1 = macro_f1(
                Sg["label_va"],
                predict(model, Sg["X_va"]),
            )
            test_f1 = macro_f1(
                Sg["label_te"],
                predict(model, Sg["X_te"]),
            )

            robust_rows.append({
                "seed": seed,
                "rounds": rounds,
                "configuration": config["name"],
                "method": config["method"],
                "mu": config["mu"],
                "validation_macro_f1": validation_f1,
                "test_macro_f1": test_f1,
            })
            unified_models[
                (seed, rounds, config["name"])
            ] = model

robust_df = pd.DataFrame(robust_rows)
robust_df.to_csv("robust_fl_results.csv", index=False)

# Select one algorithm, mu, and number of rounds using validation only.
validation_summary = (
    robust_df
    .groupby(
        ["rounds", "configuration", "method"],
        as_index=False,
        dropna=False,
    )
    .agg(
        validation_mean=("validation_macro_f1", "mean"),
        validation_std=("validation_macro_f1", "std"),
    )
    .sort_values(
        ["validation_mean", "validation_std"],
        ascending=[False, True],
    )
)
validation_summary.to_csv(
    "robust_validation_summary.csv",
    index=False,
)

selected = validation_summary.iloc[0]
selected_rounds = int(selected["rounds"])
selected_configuration = selected["configuration"]
selected_method = selected["method"]
selected_config = next(
    config for config in candidate_configs
    if config["name"] == selected_configuration
)
selected_mu = (
    selected_config["mu"]
    if selected_config["mu"] is not None
    else FL_MU
)

print("\nConfiguration selected on mean validation F1")
print(f"  Method       : {selected_configuration}")
print(f"  Rounds       : {selected_rounds}")
print(
    f"  Validation F1: "
    f"{selected['validation_mean']:.3f} "
    f"± {selected['validation_std']:.3f}"
)

# Final test performance of the validation-selected configuration.
selected_test = robust_df[
    (robust_df["rounds"] == selected_rounds)
    & (
        robust_df["configuration"]
        == selected_configuration
    )
]["test_macro_f1"].to_numpy()

print(
    f"  Test F1      : {selected_test.mean():.3f} "
    f"± {selected_test.std(ddof=1):.3f}"
)

# %%
# ============================================================
# ROBUST FEDERATED-VS-CENTRALIZED COMPARISON
# Same five seeds and same selected training budget.
# FedProx mu is chosen using validation results only.
# ============================================================
best_fedprox_row = (
    validation_summary[
        (validation_summary["rounds"] == selected_rounds)
        & (validation_summary["method"] == "FedProx")
    ]
    .sort_values(
        ["validation_mean", "validation_std"],
        ascending=[False, True],
    )
    .iloc[0]
)
best_fedprox_configuration = best_fedprox_row["configuration"]

reported_fl_configurations = {
    "FedAvg": "FedAvg",
    "FedProx": best_fedprox_configuration,
    "FedLC": "FedLC",
}

robust_model_comparison = {}
for display_name, configuration in reported_fl_configurations.items():
    values = robust_df[
        (robust_df["rounds"] == selected_rounds)
        & (robust_df["configuration"] == configuration)
    ]["test_macro_f1"].to_numpy()
    robust_model_comparison[display_name] = values

print(
    "\nTraining centralized baselines with the same five seeds "
    f"({selected_rounds * FL_EPOCHS} epochs)..."
)
centralized_test_scores = []
centralized_rows = []
for seed in ROBUST_SEEDS:
    SEED = seed
    torch.manual_seed(seed)
    np.random.seed(seed)

    centralized_model = train_mlp(
        Sg["X_tr"],
        Sg["y_tr"],
        n_classes,
        epochs=selected_rounds * FL_EPOCHS,
        lr=FL_LR,
    )
    centralized_f1 = macro_f1(
        Sg["label_te"],
        predict(centralized_model, Sg["X_te"]),
    )
    centralized_test_scores.append(centralized_f1)
    centralized_rows.append({
        "seed": seed,
        "rounds": selected_rounds,
        "epochs": selected_rounds * FL_EPOCHS,
        "configuration": "Centralized",
        "test_macro_f1": centralized_f1,
    })

robust_model_comparison["Centralized"] = np.asarray(
    centralized_test_scores
)

comparison_rows = []
for method_name, values in robust_model_comparison.items():
    for seed, value in zip(ROBUST_SEEDS, values):
        comparison_rows.append({
            "seed": seed,
            "method": method_name,
            "test_macro_f1": value,
        })

robust_model_comparison_df = pd.DataFrame(comparison_rows)
robust_model_comparison_df.to_csv(
    "robust_models_vs_centralized.csv",
    index=False,
)

print("\nRobust federated and centralized comparison")
print(
    f"  FedProx configuration selected on validation: "
    f"{best_fedprox_configuration}"
)
for method_name, values in robust_model_comparison.items():
    print(
        f"  {method_name:<11}: "
        f"{values.mean():.3f} ± {values.std(ddof=1):.3f}"
    )

comparison_names = [
    "FedAvg", "FedProx", "FedLC", "Centralized"
]
comparison_means = [
    robust_model_comparison[name].mean()
    for name in comparison_names
]
comparison_stds = [
    robust_model_comparison[name].std(ddof=1)
    for name in comparison_names
]

fig, ax = plt.subplots(figsize=(7, 4.7))
bars = ax.bar(
    comparison_names,
    comparison_means,
    yerr=comparison_stds,
    capsize=5,
    color=["#4C72B0", "#DD8452", "#55A868", "#C44E52"],
    width=0.6,
)
for bar, mean, std in zip(
    bars, comparison_means, comparison_stds
):
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        mean + std + 0.018,
        f"{mean:.3f} ± {std:.3f}",
        ha="center",
        va="bottom",
        fontsize=9,
        fontweight="bold",
    )
ax.set_ylim(0, 1.08)
ax.set_ylabel("Test macro F1")
ax.set_title(
    "Federated and centralized model comparison\n"
    f"({selected_rounds} rounds, five training seeds)"
)
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(
    "fig2_models_vs_centralized.pdf",
    bbox_inches="tight",
)
plt.savefig(
    "fig2_models_vs_centralized.png",
    dpi=300,
    bbox_inches="tight",
)
plt.show(); print("Robust Fig 2 saved.")

# %%
# Capacity-matched cross-plane comparison for the selected
# algorithm/hyperparameters. No test result is used for selection.
robust_unified_f1 = []
robust_compact_fusion_f1 = []
robust_crossplane_rows = []

print("\nRunning capacity-matched cross-plane comparison...")
for seed in ROBUST_SEEDS:
    SEED = seed
    torch.manual_seed(seed)
    np.random.seed(seed)

    unified_model = unified_models[
        (seed, selected_rounds, selected_configuration)
    ]

    compact_n3 = train_selected_fl(
        selected_method,
        S1["X_tr_n3"], S1["y_tr"],
        S2["X_tr_n3"], S2["y_tr"],
        hidden=(26, 14),
        rounds=selected_rounds,
        mu=selected_mu,
        tau=FL_TAU,
    )
    compact_n4 = train_selected_fl(
        selected_method,
        S1["X_tr_n4"], S1["y_tr"],
        S2["X_tr_n4"], S2["y_tr"],
        hidden=(26, 14),
        rounds=selected_rounds,
        mu=selected_mu,
        tau=FL_TAU,
    )

    unified_f1 = macro_f1(
        Sg["label_te"],
        predict(unified_model, Sg["X_te"]),
    )
    compact_fusion_f1 = macro_f1(
        Sg["label_te"],
        predict_fusion(
            compact_n3,
            compact_n4,
            Sg["X_te_n3"],
            Sg["X_te_n4"],
        ),
    )

    robust_unified_f1.append(unified_f1)
    robust_compact_fusion_f1.append(compact_fusion_f1)
    robust_crossplane_rows.extend([
        {
            "seed": seed,
            "representation": "Unified N3+N4",
            "test_macro_f1": unified_f1,
            "parameters": parameter_count(unified_model),
        },
        {
            "seed": seed,
            "representation": "Compact N3/N4 fusion",
            "test_macro_f1": compact_fusion_f1,
            "parameters": (
                parameter_count(compact_n3)
                + parameter_count(compact_n4)
            ),
        },
    ])

robust_crossplane_df = pd.DataFrame(robust_crossplane_rows)
robust_crossplane_df.to_csv(
    "robust_crossplane_results.csv",
    index=False,
)

unified_mean = np.mean(robust_unified_f1)
unified_std = np.std(robust_unified_f1, ddof=1)
fusion_mean = np.mean(robust_compact_fusion_f1)
fusion_std = np.std(robust_compact_fusion_f1, ddof=1)

print("\nRobust capacity-matched test comparison")
print(
    f"  Unified N3+N4       : "
    f"{unified_mean:.3f} ± {unified_std:.3f}"
)
print(
    f"  Compact N3/N4 fusion: "
    f"{fusion_mean:.3f} ± {fusion_std:.3f}"
)
print(
    f"  Mean difference      : "
    f"{unified_mean - fusion_mean:+.3f}"
)

# FedProx mu sensitivity on validation data.
fedprox_summary = (
    robust_df[robust_df["method"] == "FedProx"]
    .groupby(["rounds", "mu"], as_index=False)
    .agg(
        mean_f1=("validation_macro_f1", "mean"),
        std_f1=("validation_macro_f1", "std"),
    )
)

fig, ax = plt.subplots(figsize=(7, 4.5))
for rounds in ROUND_GRID:
    values = fedprox_summary[
        fedprox_summary["rounds"] == rounds
    ].sort_values("mu")
    ax.errorbar(
        values["mu"],
        values["mean_f1"],
        yerr=values["std_f1"],
        marker="o",
        capsize=3,
        label=f"{rounds} rounds",
    )
ax.set_xscale("log")
ax.set_ylim(0, 1.05)
ax.set_xlabel(r"FedProx $\mu$")
ax.set_ylabel("Validation macro F1")
ax.set_title("FedProx sensitivity across training seeds")
ax.grid(alpha=0.3)
ax.legend()
plt.tight_layout()
plt.savefig(
    "fig4_fedprox_mu_sensitivity.pdf",
    bbox_inches="tight",
)
plt.savefig(
    "fig4_fedprox_mu_sensitivity.png",
    dpi=300,
    bbox_inches="tight",
)
plt.show()

# Final parameter-matched comparison with variability.
fig, ax = plt.subplots(figsize=(6, 4.5))
names = ["Unified N3+N4", "Compact N3/N4\nlogit fusion"]
means = [unified_mean, fusion_mean]
stds = [unified_std, fusion_std]
bars = ax.bar(
    names,
    means,
    yerr=stds,
    capsize=5,
    color=["#4C72B0", "#8172B3"],
    width=0.58,
)
for bar, mean, std in zip(bars, means, stds):
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        mean + std + 0.02,
        f"{mean:.3f} ± {std:.3f}",
        ha="center",
        va="bottom",
        fontweight="bold",
    )
ax.set_ylim(0, 1.08)
ax.set_ylabel("Test macro F1")
ax.set_title(
    "Parameter-matched cross-plane comparison\n"
    f"({selected_configuration}, {selected_rounds} rounds)"
)
ax.grid(axis="y", alpha=0.3)
plt.tight_layout()
plt.savefig(
    "fig5_robust_crossplane_comparison.pdf",
    bbox_inches="tight",
)
plt.savefig(
    "fig5_robust_crossplane_comparison.png",
    dpi=300,
    bbox_inches="tight",
)
plt.show()

print("\nAll figures saved (PDF + PNG 300 dpi).")
