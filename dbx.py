"""Shared helper: query Databricks through the CLI profile and return pandas DataFrames.

Authentication comes from the `igaming` CLI profile (OAuth). No secret lives in this repo.
"""
import json
import subprocess
import time

import pandas as pd

PROFILE = "igaming"
WAREHOUSE = "c7512e31ce256bc1"
TYPE_MAP = {"INT": "int64", "LONG": "int64", "DOUBLE": "float64", "DECIMAL": "float64", "FLOAT": "float64"}


def _api(method, path, body=None, retries=4):
    cmd = ["databricks", "api", method, path, "-p", PROFILE]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    for attempt in range(retries):
        out = subprocess.run(cmd, capture_output=True, text=True)
        if out.returncode == 0:
            return json.loads(out.stdout)
        if attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(out.stderr.strip() or out.stdout.strip())


def query(sql: str) -> pd.DataFrame:
    """Run a SQL statement and return the full result as a DataFrame."""
    r = _api("post", "/api/2.0/sql/statements",
             {"warehouse_id": WAREHOUSE, "wait_timeout": "50s", "statement": sql,
              "format": "JSON_ARRAY", "disposition": "INLINE"})
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(3)
        r = _api("get", f"/api/2.0/sql/statements/{r['statement_id']}")
    if r["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(r["status"].get("error", {}).get("message", r["status"]["state"]))

    cols = r["manifest"]["schema"]["columns"]
    rows = list(r["result"].get("data_array", []))
    # Large results come back in chunks.
    for chunk in range(1, r["manifest"].get("total_chunk_count", 1)):
        rows += _api("get", f"/api/2.0/sql/statements/{r['statement_id']}/result/chunks/{chunk}").get("data_array", [])

    df = pd.DataFrame(rows, columns=[c["name"] for c in cols])
    for c in cols:
        t = TYPE_MAP.get(c["type_name"])
        if t:
            df[c["name"]] = pd.to_numeric(df[c["name"]], errors="coerce")
        elif c["type_name"] in ("DATE", "TIMESTAMP"):
            df[c["name"]] = pd.to_datetime(df[c["name"]], errors="coerce")
    return df
