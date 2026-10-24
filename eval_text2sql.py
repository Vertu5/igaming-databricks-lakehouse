"""Evaluation of the Text-to-SQL layer.

Part A (offline, no Databricks needed): guardrail tests, i.e. does the validator block
hallucinated columns, unknown tables, writes, multi-statements, and enforce LIMIT?

Part B (needs Databricks, and a real LLM for a meaningful score): execution accuracy.
The generated SQL and the reference SQL are both executed, and result sets must match.
With the offline demo provider this only checks that the pipeline works end to end.
Run with a real model:  GEMINI_API_KEY=... python eval_text2sql.py --live
"""
import json
import os
import sys

import pandas as pd

import text2sql as t2s

T = "gold_player_stats"
# (question, reference SQL)
EVAL_SET = [
    ("Who are the 10 players with the highest turnover?",
     f"SELECT player_id, turnover FROM {T} ORDER BY turnover DESC LIMIT 10"),
    ("Which acquisition channel generates the most revenue?",
     "SELECT acquisition_channel, ggr FROM gold_channel_performance ORDER BY ggr DESC LIMIT 1"),
    ("How many players are there in each segment?",
     f"SELECT segment, COUNT(*) AS players FROM {T} GROUP BY segment"),
    ("What is the total gross gaming revenue?",
     f"SELECT ROUND(SUM(ggr), 2) AS total_ggr FROM {T}"),
    ("How many sessions have a high risk level?",
     "SELECT COUNT(*) AS n FROM gold_risk_sessions WHERE risk_level = 'High'"),
    ("What is the average turnover of VIP players?",
     f"SELECT ROUND(AVG(turnover), 2) AS avg_turnover FROM {T} WHERE segment = 'VIP'"),
    ("How many players use each device type?",
     f"SELECT device_pref, COUNT(*) AS players FROM {T} GROUP BY device_pref"),
    ("Which channel has the best activation rate?",
     "SELECT acquisition_channel, activation_rate FROM gold_channel_performance ORDER BY activation_rate DESC LIMIT 1"),
    ("How many high value players are at risk of leaving?",
     f"SELECT COUNT(*) AS n FROM {T} WHERE segment = 'At risk (high value)'"),
    ("What share of sessions have a geo mismatch?",
     "SELECT ROUND(AVG(geo_mismatch), 4) AS rate FROM gold_risk_sessions"),
    ("Which channel earns the least per registered player?",
     "SELECT acquisition_channel, ggr_per_registered_player FROM gold_channel_performance ORDER BY ggr_per_registered_player ASC LIMIT 1"),
    ("How many players bought a feature more than 50 times?",
     f"SELECT COUNT(*) AS n FROM {T} WHERE feature_buys > 50"),
    ("How many registered players never played?",
     f"SELECT COUNT(*) AS n FROM {T} WHERE segment = 'No activity'"),
    ("What is the average number of sessions of a Champion?",
     f"SELECT ROUND(AVG(sessions), 2) AS avg_sessions FROM {T} WHERE segment = 'Champion'"),
    ("What is the total turnover by operating system?",
     f"SELECT os, ROUND(SUM(turnover), 2) AS turnover FROM {T} GROUP BY os"),
]

# (label, SQL, must_be_accepted)
GUARD_CASES = [
    ("valid select", f"SELECT player_id FROM {T}", True),
    ("qualified table", f"SELECT player_id FROM workspace.igaming.{T}", True),
    ("alias and cte", f"WITH x AS (SELECT segment, SUM(ggr) AS g FROM {T} GROUP BY segment) SELECT segment, g FROM x", True),
    ("hallucinated column", f"SELECT lifetime_value FROM {T}", False),
    ("hallucinated column in where", f"SELECT player_id FROM {T} WHERE vip_level = 3", False),
    ("unknown table", "SELECT * FROM gold_player_bonuses", False),
    ("raw layer access", "SELECT * FROM bronze_spins", False),
    ("other schema", "SELECT * FROM system.information_schema.tables", False),
    ("drop table", f"DROP TABLE {T}", False),
    ("delete", f"DELETE FROM {T}", False),
    ("update", f"UPDATE {T} SET ggr = 0", False),
    ("two statements", f"SELECT 1 FROM {T}; DROP TABLE {T}", False),
    ("not a query", "OPTIMIZE gold_player_stats", False),
]


def part_a():
    print("== A. Guardrail tests (offline)")
    passed = 0
    for label, sql, expect_ok in GUARD_CASES:
        v = t2s.validate(sql)
        good = v.ok == expect_ok
        passed += good
        print(f"  [{'PASS' if good else 'FAIL'}] {label:32s} -> {'accepted' if v.ok else 'rejected: ' + v.reason}")
    limit_ok = "LIMIT 100" in t2s.validate(f"SELECT player_id FROM {T}").sql
    print(f"  [{'PASS' if limit_ok else 'FAIL'}] LIMIT is enforced when missing")
    total = len(GUARD_CASES) + 1
    passed += limit_ok
    print(f"  Guardrails: {passed}/{total}")
    return {"guardrail_passed": int(passed), "guardrail_total": total}


def same_result(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if a.shape != b.shape:
        return False
    key = lambda d: d.round(4).astype(str).sort_values(list(d.columns)).reset_index(drop=True)
    # compare values only, ignore column names (aliases may differ)
    a2, b2 = a.copy(), b.copy()
    a2.columns = b2.columns = range(a2.shape[1])
    return key(a2).equals(key(b2))


def part_b(live: bool):
    from dbx import query
    provider = t2s.GeminiProvider() if live else t2s.OfflineProvider(dict(EVAL_SET))
    print(f"\n== B. Execution accuracy ({provider.name})")
    ok = 0
    rows = []
    for q, ref in EVAL_SET:
        res = t2s.ask(q, provider)
        match = False
        if res["status"] == "ok":
            ref_v = t2s.validate(ref)
            match = same_result(pd.DataFrame(res["rows"]), query(ref_v.sql))
        ok += match
        rows.append({"question": q, "status": res["status"], "match": bool(match), "sql": res.get("sql")})
        print(f"  [{'PASS' if match else 'FAIL'}] {q}")
    print(f"  Execution accuracy: {ok}/{len(EVAL_SET)}")
    return {"provider": provider.name, "accuracy_passed": ok, "accuracy_total": len(EVAL_SET), "details": rows}


if __name__ == "__main__":
    live = "--live" in sys.argv
    if live and "GEMINI_API_KEY" not in os.environ:
        sys.exit("Set GEMINI_API_KEY to run the live evaluation.")
    out = {**part_a(), **part_b(live)}
    os.makedirs("data", exist_ok=True)
    with open("data/text2sql_eval.json", "w") as f:
        json.dump(out, f, indent=2)
