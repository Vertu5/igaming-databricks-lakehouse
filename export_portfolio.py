"""Export Gold-layer results to a static JSON file for the portfolio.

Each entry keeps the SQL that produced it, so the site can show it ("Under the hood").
No credential is written: the site only consumes the resulting JSON.
Run: python export_portfolio.py  -> data/portfolio.json
"""
import json
import os

from dbx import query

G = "workspace.igaming"

QUERIES = {
    "kpis": f"""
SELECT
  (SELECT COUNT(*) FROM {G}.gold_player_stats) AS registered_players,
  (SELECT COUNT(*) FROM {G}.gold_player_value) AS active_players,
  (SELECT COUNT(*) FROM {G}.bronze_spins) AS spins,
  (SELECT COUNT(*) FROM {G}.bronze_sessions) AS sessions,
  (SELECT ROUND(SUM(bet_amount), 0) FROM {G}.bronze_spins) AS turnover,
  (SELECT ROUND(SUM(bet_amount - win_amount), 0) FROM {G}.bronze_spins) AS ggr,
  (SELECT ROUND(SUM(win_amount) / SUM(bet_amount), 4) FROM {G}.bronze_spins) AS rtp""",
    "channels": f"""
SELECT acquisition_channel, registered_players, active_players, activation_rate,
       ggr, ggr_per_registered_player, hold_rate
FROM {G}.gold_channel_performance
ORDER BY ggr_per_registered_player DESC""",
    "segments": f"""
SELECT segment, COUNT(*) AS players,
       ROUND(SUM(turnover), 0) AS turnover,
       ROUND(SUM(ggr), 0) AS ggr,
       ROUND(SUM(ggr) / SUM(SUM(ggr)) OVER (), 4) AS share_of_ggr
FROM {G}.gold_player_value
GROUP BY segment
ORDER BY ggr DESC""",
    "pareto": f"""
SELECT ROUND(decile / 10.0, 1) AS top_share_of_players,
       ROUND(SUM(SUM(turnover)) OVER (ORDER BY decile) / SUM(SUM(turnover)) OVER (), 4) AS cumulative_share_of_turnover
FROM (
  SELECT turnover, NTILE(10) OVER (ORDER BY turnover DESC) AS decile
  FROM {G}.gold_player_value
)
GROUP BY decile
ORDER BY decile""",
    "net_winners": f"""
SELECT COUNT(*) AS active_players,
       SUM(CASE WHEN ggr < 0 THEN 1 ELSE 0 END) AS net_winners,
       ROUND(AVG(CASE WHEN ggr < 0 THEN 1.0 ELSE 0.0 END), 4) AS net_winner_share
FROM {G}.gold_player_value""",
    "top_vip": f"""
SELECT player_id, acquisition_channel, country, turnover, ggr, sessions
FROM {G}.gold_player_stats
WHERE segment = 'VIP'
ORDER BY turnover DESC
LIMIT 10""",
    "risk_levels": f"""
SELECT risk_level, COUNT(*) AS sessions, COUNT(DISTINCT player_id) AS players
FROM {G}.gold_risk_sessions
GROUP BY risk_level
ORDER BY CASE risk_level WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 WHEN 'Low' THEN 3 ELSE 4 END""",
    "risk_flags": f"""
SELECT 'Geo mismatch' AS flag, SUM(geo_mismatch) AS sessions FROM {G}.gold_risk_sessions
UNION ALL SELECT 'Long session', SUM(flag_long_session) FROM {G}.gold_risk_sessions
UNION ALL SELECT 'High pace', SUM(flag_high_pace) FROM {G}.gold_risk_sessions
UNION ALL SELECT 'High bet', SUM(flag_high_bet) FROM {G}.gold_risk_sessions
UNION ALL SELECT 'Shared device', SUM(flag_shared_device) FROM {G}.gold_risk_sessions
UNION ALL SELECT 'Shared IP', SUM(flag_shared_ip) FROM {G}.gold_risk_sessions""",
    "monthly": f"""
SELECT DATE_TRUNC('month', ts) AS month,
       ROUND(SUM(bet_amount), 0) AS turnover,
       ROUND(SUM(bet_amount - win_amount), 0) AS ggr,
       COUNT(DISTINCT player_id) AS active_players
FROM {G}.bronze_spins
GROUP BY DATE_TRUNC('month', ts)
ORDER BY month""",
    "volatility": f"""
SELECT slot_volatility, COUNT(*) AS spins,
       ROUND(SUM(bet_amount), 0) AS turnover,
       ROUND(SUM(bet_amount - win_amount), 0) AS ggr,
       ROUND(SUM(win_amount) / SUM(bet_amount), 4) AS rtp
FROM {G}.bronze_spins
GROUP BY slot_volatility
ORDER BY turnover DESC""",
}


def main():
    out = {}
    for name, sql in QUERIES.items():
        df = query(sql)
        for c in df.columns:
            if str(df[c].dtype).startswith("datetime"):
                df[c] = df[c].dt.strftime("%Y-%m-%d")
        out[name] = {"sql": sql.strip(), "rows": json.loads(df.to_json(orient="records"))}
        print(f"{name}: {len(df)} rows")

    # Data-science outputs produced by datascience.py and eval_text2sql.py.
    for key, fname in [("clusters", "clusters.json"), ("anomalies", "anomaly_summary.json"),
                       ("churn", "churn_model.json"), ("text2sql_eval", "text2sql_eval.json")]:
        path = os.path.join("data", fname)
        if os.path.exists(path):
            with open(path) as f:
                out[key] = json.load(f)

    with open(os.path.join("data", "portfolio.json"), "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("written data/portfolio.json")


if __name__ == "__main__":
    main()
