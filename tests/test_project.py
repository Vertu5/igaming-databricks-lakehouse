"""Local CI checks. Run with:  make check   (also run automatically by the pre-push hook).

1. Text-to-SQL guardrails behave as specified.
2. Every SQL file of the pipeline parses (parameter markers are neutralised first).
3. Every file referenced by the Databricks job exists AND is actually synced to the workspace.
   (Catches the case where a sync exclusion silently drops a file the job needs.)
"""
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import sqlglot

import eval_text2sql as ev
import text2sql as t2s

ROOT = Path(__file__).resolve().parent.parent
JOB_FILE = ROOT / "resources" / "igaming_pipeline.yml"


def job_task_paths():
    text = JOB_FILE.read_text()
    sqls = re.findall(r"path:\s*(\S+\.sql)", text)
    nbs = re.findall(r"notebook_path:\s*(\S+\.py)", text)
    return [(JOB_FILE.parent / p).resolve() for p in (sqls + nbs)]


class TestGuardrails(unittest.TestCase):
    def test_guard_cases(self):
        for label, sql, expect_ok in ev.GUARD_CASES:
            with self.subTest(label):
                self.assertEqual(t2s.validate(sql).ok, expect_ok)

    def test_limit_is_enforced(self):
        self.assertIn("LIMIT 100", t2s.validate("SELECT player_id FROM gold_player_stats").sql)

    def test_self_correction_loop(self):
        import pandas as pd

        class FlakyLLM:
            def __init__(self):
                self.calls = []

            def generate(self, question, feedback=None):
                self.calls.append((question, feedback))
                if feedback is None:
                    # 1st attempt: hallucinates non-existent column `revenue`
                    return "SELECT player_id, revenue FROM gold_player_stats"
                # 2nd attempt: uses feedback to fix column to `ggr`
                return "SELECT player_id, ggr FROM gold_player_stats LIMIT 5"

        executed = []
        def fake_run(sql):
            executed.append(sql)
            return pd.DataFrame([{"player_id": 1, "ggr": 100.0}])

        llm = FlakyLLM()
        res = t2s.ask("Top players by revenue", llm, run_query=fake_run, dry_run_dev=True)
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("revenue", llm.calls[1][1][1])
        # Verify dry_run_dev ran against igaming_dev first, then igaming prod
        self.assertEqual(len(executed), 2)
        self.assertIn("igaming_dev", executed[0])
        self.assertIn("workspace.igaming.gold_player_stats", executed[1])


class TestSql(unittest.TestCase):
    @staticmethod
    def neutralise(text):
        text = re.sub(r"IDENTIFIER\(:(\w+)\)", r"\1_value", text)       # IDENTIFIER(:schema) -> schema_value
        return re.sub(r"CAST\(:(\w+) AS INT\)", "1", text)               # CAST(:sample_mod AS INT) -> 1

    def test_all_sql_files_parse(self):
        files = list((ROOT / "src" / "sql").rglob("*.sql"))
        self.assertGreaterEqual(len(files), 7)
        for f in files:
            with self.subTest(f.name):
                body = "\n".join(l.split("--")[0] for l in self.neutralise(f.read_text()).splitlines())
                stmts = [s for s in sqlglot.parse(body, read="databricks") if s is not None]
                self.assertGreater(len(stmts), 0)

    def test_every_quality_file_asserts_something(self):
        for f in (ROOT / "src" / "sql" / "quality").glob("test_*.sql"):
            with self.subTest(f.name):
                self.assertIn("assert_true", f.read_text())


class TestJobWiring(unittest.TestCase):
    def test_job_files_exist(self):
        paths = job_task_paths()
        self.assertEqual(len(paths), 8)
        for p in paths:
            with self.subTest(p.name):
                self.assertTrue(p.exists(), f"{p} referenced by the job does not exist")

    @unittest.skipUnless(shutil.which("databricks"), "databricks CLI not installed")
    def test_job_files_are_synced(self):
        out = subprocess.run(["databricks", "bundle", "sync", "-t", "dev", "-p", "igaming", "--dry-run", "--full"],
                             cwd=ROOT, capture_output=True, text=True)
        if out.returncode != 0:
            self.skipTest("cannot reach Databricks: " + out.stderr.strip()[:120])
        synced = out.stdout
        for p in job_task_paths():
            with self.subTest(p.name):
                self.assertIn(str(p.relative_to(ROOT)), synced, f"{p.name} would NOT be uploaded to the workspace")



if __name__ == "__main__":
    unittest.main()
