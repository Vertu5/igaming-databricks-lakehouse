"""Data science layer on top of the Gold tables.

1. KMeans behavioural clustering of active players (beyond rule-based RFM).
2. IsolationForest anomaly detection on sessions (complements the rule-based risk score).
3. Churn indicator: logistic regression predicting inactivity in the last 30 days.

Outputs JSON/CSV files in `data/` consumed by the report and the portfolio.
"""
import json
import os

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

from dbx import query

OUT = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUT, exist_ok=True)
SEED = 42


def save(name, obj):
    with open(os.path.join(OUT, name), "w") as f:
        json.dump(obj, f, indent=2, default=str)


def clustering():
    df = query("""
        SELECT player_id, spins, sessions, active_days, turnover, ggr, avg_bet, max_bet,
               feature_buys, recency_days, segment
        FROM workspace.igaming.gold_player_value
    """)
    feats = ["sessions", "active_days", "turnover", "avg_bet", "max_bet", "feature_buys", "recency_days"]
    X = df[feats].copy()
    # Heavy-tailed variables: log transform before scaling.
    for c in ["sessions", "turnover", "avg_bet", "max_bet", "feature_buys"]:
        X[c] = np.log1p(X[c])
    Xs = StandardScaler().fit_transform(X)

    km = KMeans(n_clusters=4, n_init=10, random_state=SEED).fit(Xs)
    df["cluster"] = km.labels_
    prof = (df.groupby("cluster")
              .agg(players=("player_id", "count"), avg_turnover=("turnover", "mean"),
                   avg_ggr=("ggr", "mean"), avg_sessions=("sessions", "mean"),
                   avg_bet=("avg_bet", "mean"), feature_buys=("feature_buys", "mean"),
                   recency_days=("recency_days", "mean"),
                   total_ggr=("ggr", "sum"))
              .round(2).reset_index())
    prof["share_of_players"] = (prof["players"] / prof["players"].sum()).round(4)
    prof["share_of_ggr"] = (prof["total_ggr"] / prof["total_ggr"].sum()).round(4)
    save("clusters.json", prof.to_dict("records"))
    return prof


def anomalies():
    df = query("""
        SELECT session_id, player_id, duration_sec, spins_per_min, max_bet, num_spins,
               geo_mismatch, flag_shared_device, flag_shared_ip, risk_score, risk_level
        FROM workspace.igaming.gold_risk_sessions
    """)
    feats = ["duration_sec", "spins_per_min", "max_bet", "num_spins"]
    X = df[feats].copy()
    for c in feats:
        X[c] = np.log1p(X[c])
    iso = IsolationForest(n_estimators=200, contamination=0.01, random_state=SEED).fit(X)
    df["anomaly_score"] = -iso.score_samples(X)
    df["is_anomaly"] = (iso.predict(X) == -1).astype(int)

    # How much does the ML view agree with the rule-based view?
    rules_flag = df["risk_level"].isin(["High", "Medium"]).astype(int)
    both = int(((df["is_anomaly"] == 1) & (rules_flag == 1)).sum())
    summary = {
        "sessions": int(len(df)),
        "ml_anomalies": int(df["is_anomaly"].sum()),
        "rule_based_high_medium": int(rules_flag.sum()),
        "overlap": both,
        "ml_only": int(((df["is_anomaly"] == 1) & (rules_flag == 0)).sum()),
        "rules_only": int(((df["is_anomaly"] == 0) & (rules_flag == 1)).sum()),
    }
    save("anomaly_summary.json", summary)
    df.sort_values("anomaly_score", ascending=False).head(50).to_csv(
        os.path.join(OUT, "top_anomalies.csv"), index=False)
    return summary


def churn():
    # Behaviour observed up to a cutoff, label = no activity in the last 30 days of the data.
    df = query("""
        WITH bounds AS (SELECT MAX(CAST(ts AS DATE)) AS max_d FROM workspace.igaming.bronze_spins),
        feat AS (
            SELECT s.player_id,
                   COUNT(*) AS spins, COUNT(DISTINCT s.session_id) AS sessions,
                   COUNT(DISTINCT CAST(s.ts AS DATE)) AS active_days,
                   SUM(s.bet_amount) AS turnover, AVG(s.bet_amount) AS avg_bet,
                   SUM(CASE WHEN s.feature_buy = 1 THEN 1 ELSE 0 END) AS feature_buys,
                   DATEDIFF(MAX(CAST(s.ts AS DATE)), MIN(CAST(s.ts AS DATE))) AS tenure_days,
                   DATEDIFF(DATE_SUB((SELECT max_d FROM bounds), 30), MAX(CAST(s.ts AS DATE))) AS days_idle_before_cutoff
            FROM workspace.igaming.bronze_spins s
            CROSS JOIN bounds b
            WHERE CAST(s.ts AS DATE) <= DATE_SUB(b.max_d, 30) AND s.bet_amount > 0
            GROUP BY s.player_id
        ),
        label AS (
            SELECT DISTINCT player_id FROM workspace.igaming.bronze_spins s
            CROSS JOIN bounds b WHERE CAST(s.ts AS DATE) > DATE_SUB(b.max_d, 30)
        )
        SELECT f.*, CASE WHEN l.player_id IS NULL THEN 1 ELSE 0 END AS churned
        FROM feat f LEFT JOIN label l ON f.player_id = l.player_id
    """)
    feats = ["spins", "sessions", "active_days", "turnover", "avg_bet", "feature_buys",
             "tenure_days", "days_idle_before_cutoff"]
    X = df[feats].copy()
    for c in ["spins", "sessions", "turnover", "avg_bet", "feature_buys"]:
        X[c] = np.log1p(X[c])
    y = df["churned"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.3, random_state=SEED, stratify=y)
    sc = StandardScaler().fit(Xtr)
    model = LogisticRegression(max_iter=1000).fit(sc.transform(Xtr), ytr)
    auc = roc_auc_score(yte, model.predict_proba(sc.transform(Xte))[:, 1])
    coefs = dict(zip(feats, model.coef_[0].round(3)))
    res = {"players": int(len(df)), "churn_rate": round(float(y.mean()), 4),
           "auc_test": round(float(auc), 4), "standardized_coefficients": coefs}
    save("churn_model.json", res)
    return res


if __name__ == "__main__":
    print("== clusters"); print(clustering().to_string(index=False))
    print("== anomalies"); print(anomalies())
    print("== churn"); print(churn())
