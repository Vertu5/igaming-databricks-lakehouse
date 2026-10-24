"""Text-to-SQL over the Gold layer, with guardrails.

Pipeline: question -> LLM (schema-aware prompt) -> SQL -> validate -> execute on Databricks.

Guardrails (validate): a single read-only SELECT, only Gold tables, every column must exist
(hallucinated columns are rejected, never executed), tables are fully qualified, LIMIT is enforced.
The user gets an explicit refusal instead of a wrong answer.

LLM providers:
  - GeminiProvider : needs env GEMINI_API_KEY (and optional GEMINI_MODEL). Key never stored in code.
  - OfflineProvider: answers a fixed set of known questions (demo mode, no key required).
"""
import json
import os
import re
import urllib.request
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

CATALOG, SCHEMA_NAME = "workspace", "igaming"
MAX_ROWS = 100

# Table -> {column: description}. This is the only context given to the LLM.
SCHEMA = {
    "gold_player_stats": {
        "player_id": "player identifier",
        "signup_ts": "signup timestamp",
        "country": "declared country code",
        "acquisition_channel": "organic, affiliate, paid_social, paid_search or referral",
        "device_pref": "mobile or desktop",
        "os": "operating system",
        "segment": "VIP, Champion, Loyal, 'At risk (high value)', Dormant, Casual or 'No activity'",
        "sessions": "number of sessions",
        "spins": "number of spins",
        "turnover": "total amount bet",
        "ggr": "gross gaming revenue = bets - wins (money kept by the casino)",
        "avg_bet": "average bet amount",
        "feature_buys": "number of bonus feature purchases",
        "last_play_date": "date of last play",
        "recency_days": "days since last play (relative to the end of the data)",
        "risky_sessions": "number of sessions with High or Medium risk",
        "max_risk_score": "highest session risk score of the player (0-6)",
    },
    "gold_channel_performance": {
        "acquisition_channel": "acquisition channel",
        "registered_players": "all registered players",
        "active_players": "players with at least one spin",
        "activation_rate": "active_players / registered_players",
        "spins": "total spins",
        "turnover": "total amount bet",
        "ggr": "gross gaming revenue",
        "ggr_per_registered_player": "ggr divided by registered players",
        "ggr_per_active_player": "ggr divided by active players",
        "hold_rate": "ggr / turnover",
    },
    "gold_risk_sessions": {
        "session_id": "session identifier",
        "player_id": "player identifier",
        "start_ts": "session start",
        "duration_sec": "session duration in seconds",
        "spins_per_min": "spin pace",
        "max_bet": "largest bet of the session",
        "num_spins": "spins in the session",
        "ip_country": "country resolved from the IP",
        "declared_country": "country declared by the player",
        "geo_mismatch": "1 if IP country differs from declared country",
        "flag_long_session": "1 if duration above the 99th percentile",
        "flag_high_pace": "1 if pace above the 99th percentile",
        "flag_high_bet": "1 if max bet above the 99th percentile",
        "flag_shared_device": "1 if the device is used by several players",
        "flag_shared_ip": "1 if the IP is used by several players",
        "risk_score": "sum of flags (0-6)",
        "risk_level": "None, Low, Medium or High",
    },
    "gold_player_value": {
        "player_id": "player identifier",
        "spins": "number of spins",
        "sessions": "number of sessions",
        "active_days": "distinct days played",
        "turnover": "total amount bet",
        "ggr": "gross gaming revenue",
        "avg_bet": "average bet",
        "max_bet": "largest bet",
        "feature_buys": "bonus feature purchases",
        "recency_days": "days since last play",
        "r_score": "recency score 1-5 (5 = most recent)",
        "f_score": "frequency score 1-5",
        "m_score": "monetary score 1-5",
        "turnover_pct": "percentile rank of turnover (0-1)",
        "segment": "RFM segment",
    },
    "gold_player_ml_scores": {
        "player_id": "player identifier",
        "country": "declared country code",
        "acquisition_channel": "acquisition channel",
        "rfm_segment": "RFM rule-based segment",
        "cluster_id": "KMeans behavioral cluster ID (0-3)",
        "cluster_persona": "KMeans persona: Casual Low-Stakes, Regular Multi-Game, High-Frequency Grinders, or VIP High-Rollers",
        "recency_days": "days since last play",
        "churn_probability": "30-day churn probability predicted by the @champion MLflow model (0.0 to 1.0)",
        "churn_risk_tier": "Low Risk, Medium Risk, or High Risk",
        "turnover": "total amount bet",
        "ggr": "gross gaming revenue",
        "expected_ggr_at_risk": "GREATEST(ggr, 0) * churn_probability (revenue at risk of churn)",
        "risky_sessions": "number of High or Medium risk sessions",
        "model_registry_uri": "Unity Catalog registered model URI and version (@champion)",
        "scored_at": "timestamp of batch inference run",
    },
}
ALLOWED_TABLES = set(SCHEMA)
ALLOWED_COLUMNS = {c for cols in SCHEMA.values() for c in cols}


@dataclass
class Validation:
    ok: bool
    sql: str = ""
    reason: str = ""


def validate(sql: str, target_schema: str = SCHEMA_NAME) -> Validation:
    """Return the safe, qualified SQL or the reason it was rejected."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="databricks") if s is not None]
    except sqlglot.errors.ParseError as e:
        return Validation(False, reason=f"Syntax error: {str(e)[:120]}")
    if len(statements) != 1:
        return Validation(False, reason="Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union)):
        return Validation(False, reason="Only SELECT queries are allowed.")
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Merge, exp.Command)
    if tree.find(*forbidden):
        return Validation(False, reason="Data-modifying statements are forbidden.")

    ctes = {c.alias for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.name in ctes:
            continue
        if t.catalog not in ("", CATALOG) or t.db not in ("", SCHEMA_NAME, "igaming_dev"):
            return Validation(False, reason=f"Schema not allowed: {t.catalog}.{t.db}")
        if t.name not in ALLOWED_TABLES:
            return Validation(False, reason=f"Unknown table: {t.name}")
        t.set("db", exp.to_identifier(target_schema))
        t.set("catalog", exp.to_identifier(CATALOG))

    aliases = {a.alias for a in tree.find_all(exp.Alias)} | ctes
    for c in tree.find_all(exp.Column):
        if c.name not in ALLOWED_COLUMNS and c.name not in aliases:
            return Validation(False, reason=f"Unknown column (possible hallucination): {c.name}")

    if tree.args.get("limit") is None:
        tree = tree.limit(MAX_ROWS)
    return Validation(True, sql=tree.sql(dialect="databricks"))


# Few-shot examples shown to the LLM (kept disjoint from the evaluation benchmark).
FEW_SHOT = [
    ("Who are the 5 players with the most sessions?",
     "SELECT player_id, sessions FROM gold_player_stats ORDER BY sessions DESC LIMIT 5"),
    ("How many active players does each channel have?",
     "SELECT acquisition_channel, active_players FROM gold_channel_performance"),
    ("How many sessions used a device shared by several players?",
     "SELECT SUM(flag_shared_device) AS n FROM gold_risk_sessions"),
    ("What is the average revenue of dormant players?",
     "SELECT ROUND(AVG(ggr), 2) AS avg_ggr FROM gold_player_stats WHERE segment = 'Dormant'"),
]

MAX_REPAIRS = 2


def schema_prompt() -> str:
    lines = []
    for table, cols in SCHEMA.items():
        lines.append(f"Table {CATALOG}.{SCHEMA_NAME}.{table}:")
        lines += [f"  - {name}: {desc}" for name, desc in cols.items()]
    return "\n".join(lines)


def build_prompt(question: str, feedback=None) -> str:
    shots = "\n\n".join(f"Question: {q}\nSQL: {s}" for q, s in FEW_SHOT)
    prompt = (
        "You are an expert Databricks SQL analyst for an online casino (iGaming).\n"
        "Use ONLY the tables and columns below. Never invent a column.\n"
        "Write exactly one SELECT statement. Output the SQL only, with no explanation.\n"
        "If the question cannot be answered with this schema, output exactly: CANNOT_ANSWER\n\n"
        f"{schema_prompt()}\n\nExamples:\n\n{shots}\n\nQuestion: {question}\nSQL:"
    )
    if feedback:
        bad_sql, error = feedback
        prompt += (
            f" {bad_sql}\n\nThis query failed validation or execution: {error}\n"
            "Fix it using only the columns listed above. Output the corrected SQL only, "
            "or CANNOT_ANSWER if the data cannot answer the question.\nSQL:"
        )
    return prompt


class GeminiProvider:
    name = "gemini"

    def __init__(self):
        self.key = os.environ["GEMINI_API_KEY"]
        self.model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")

    def generate(self, question: str, feedback=None) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
        body = {"contents": [{"parts": [{"text": build_prompt(question, feedback)}]}],
                "generationConfig": {"temperature": 0}}
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json", "x-goog-api-key": self.key})
        with urllib.request.urlopen(req, timeout=60) as r:
            text = json.load(r)["candidates"][0]["content"]["parts"][0]["text"]
        return re.sub(r"^```(?:sql)?|```$", "", text.strip(), flags=re.M).strip()


class OfflineProvider:
    """Demo mode: maps known questions to their SQL. Validates the pipeline and guardrails only."""
    name = "offline-demo"

    def __init__(self, known: dict[str, str]):
        self.known = {self._norm(q): s for q, s in known.items()}

    @staticmethod
    def _norm(q):
        return re.sub(r"\W+", " ", q.lower()).strip()

    def generate(self, question: str, feedback=None) -> str:
        return self.known.get(self._norm(question), "CANNOT_ANSWER")


def ask(question: str, provider, run_query=None, max_repairs: int = MAX_REPAIRS, dry_run_dev: bool = False) -> dict:
    """Full pipeline with AST guardrails, optional 5% dev-sample pre-check, and self-correction loop."""
    if run_query is None:
        from dbx import query as run_query
    feedback = None
    last = {}
    for attempt in range(1, max_repairs + 2):
        raw = provider.generate(question, feedback)
        if raw.strip() == "CANNOT_ANSWER":
            return {"status": "refused", "attempts": attempt, "sql": None,
                    "reason": "The question cannot be answered with the available data."}
        v = validate(raw, target_schema=SCHEMA_NAME)
        if not v.ok:
            last = {"status": "rejected", "reason": v.reason, "sql": raw}
            feedback = (raw, v.reason)
            continue
        try:
            if dry_run_dev:
                v_dev = validate(raw, target_schema="igaming_dev")
                run_query(v_dev.sql)
            df = run_query(v.sql)
        except Exception as e:
            last = {"status": "error", "reason": str(e)[:200], "sql": v.sql}
            feedback = (v.sql, str(e)[:200])
            continue
        return {"status": "ok", "attempts": attempt, "sql": v.sql, "rows": df.to_dict("records")}
    return {**last, "attempts": max_repairs + 1}

