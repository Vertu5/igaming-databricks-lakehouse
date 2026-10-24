#!/usr/bin/env python3
"""Run a .sql file (statements separated by ';') on the Databricks SQL warehouse via the CLI profile.

Usage: python3 run_sql.py sql/01_bronze.sql [--show]
  --show  print result rows of the last statement
"""
import json
import subprocess
import sys

PROFILE = "igaming"
WAREHOUSE = "c7512e31ce256bc1"


def api(method, path, body=None):
    cmd = ["databricks", "api", method, path, "-p", PROFILE]
    if body is not None:
        cmd += ["--json", json.dumps(body)]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(out.stderr.strip() or out.stdout.strip())
    return json.loads(out.stdout)


def run(statement):
    import time
    r = api("post", "/api/2.0/sql/statements",
            {"warehouse_id": WAREHOUSE, "wait_timeout": "50s", "statement": statement})
    while r["status"]["state"] in ("PENDING", "RUNNING"):
        time.sleep(5)
        r = api("get", f"/api/2.0/sql/statements/{r['statement_id']}")
    return r


def main():
    path = sys.argv[1]
    show = "--show" in sys.argv
    text = "\n".join(l.split("--")[0] for l in open(path))
    last = None
    for stmt in [s.strip() for s in text.split(";") if s.strip()]:
        print(f">> {stmt.splitlines()[0][:90]}")
        last = run(stmt)
        st = last["status"]
        print("   ", st["state"], st.get("error", {}).get("message", ""))
        if st["state"] != "SUCCEEDED":
            raise SystemExit(1)
    if show and last and "result" in last:
        cols = [c["name"] for c in last["manifest"]["schema"]["columns"]]
        print(" | ".join(cols))
        for row in last["result"].get("data_array", []):
            print(" | ".join(str(v) for v in row))


if __name__ == "__main__":
    main()
