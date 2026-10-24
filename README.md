# iGaming analytics on Databricks

End-to-end data project on 4.6 M slot spins: a medallion pipeline (Bronze, Silver, Gold) deployed as code on Databricks, with automated data quality gates, player segmentation, risk scoring, and a churn baseline.

Full write-up: [English](report/report_en.md) · [Français](report/rapport_fr.md)

## What it answers

| Question | Gold table |
|---|---|
| Which acquisition channel is the most profitable? | `gold_channel_performance` |
| Who are the VIPs, and which valuable players are leaving? | `gold_player_value` |
| Which sessions deserve a human review? | `gold_risk_sessions` |
| One row per player, for ad-hoc analysis | `gold_player_stats` |

## Pipeline

```
CSV (Unity Catalog volume)
   -> bronze  (faithful copy)         -> quality gate
   -> silver  (player + session + spin joined) -> quality gate
   -> gold    (business tables)       -> quality gate
```

The three layers and their gates are one Databricks **job** defined in [`resources/igaming_pipeline.yml`](resources/igaming_pipeline.yml) with Declarative Automation Bundles. A gate is a SQL file using `assert_true`: a violated rule fails the task and the next layer never runs. Gates check key uniqueness, no lost spins between layers, and that revenue and turnover reconcile between Silver and Gold.

## CI/CD without a CI server

| Step | Command | What it does |
|---|---|---|
| CI gate | `make check` | unit tests (guardrails, SQL syntax, job wiring) and `bundle validate` on both environments |
| Fast loop | `make test-dev` | deploys to `dev` and runs the pipeline on a **5 percent player sample**, in about a minute |
| CD | `make deploy-prod` | runs the CI gate, then deploys to `prod` (full data) |
| Guard | `make hooks` | runs `make check` before every `git push` |

Environments (see [`databricks.yml`](databricks.yml)): `dev` writes to schema `igaming_dev` on a sample (`player_id % 20 = 0`, applied to all three tables to keep referential integrity); `prod` writes to `igaming` on all data.

> [!NOTE]
> The CI gate is a local hook, not a hosted CI service: it protects the repository only for people who enabled the hook. Moving `make check` into a hosted workflow would be the natural next step.

One wiring test is worth noting: it dry-runs the bundle sync and fails if a file the job needs would not be uploaded. It exists because a sync exclusion once silently dropped the quality gates, and the job then failed in production.

## Setup

```bash
# 1. Databricks CLI, then authenticate (browser, OAuth, no token stored in this repo)
databricks auth login --host https://<workspace>.cloud.databricks.com --profile igaming

# 2. Land the raw CSVs in the volume (one time)
databricks fs cp players.csv  dbfs:/Volumes/workspace/igaming/raw/players.csv  -p igaming
databricks fs cp sessions.csv dbfs:/Volumes/workspace/igaming/raw/sessions.csv -p igaming
databricks fs cp spins.csv    dbfs:/Volumes/workspace/igaming/raw/spins.csv    -p igaming

# 3. Python env for the tests
python3 -m venv .venv --system-site-packages && .venv/bin/pip install sqlglot

# 4. Go
make hooks && make test-dev && make deploy-prod && make run-prod
```

Set `warehouse_id` in `databricks.yml` to your own SQL warehouse.

## Analysis and data science

| Script | Purpose |
|---|---|
| `datascience.py` | KMeans player clusters, IsolationForest session anomalies, churn logistic regression (AUC 0.75 on a held-out set) |
| `export_portfolio.py` | exports Gold results and their SQL to a static JSON for the portfolio site |
| `text2sql.py`, `eval_text2sql.py` | prototype Text-to-SQL with guardrails and an evaluation harness (see the report for what is and is not measured) |
| `analysis/sql/` | profiling and ad-hoc checks |

## Repository layout

```
databricks.yml            bundle: targets dev and prod
resources/                the Databricks job
src/sql/                  pipeline SQL (parameterised: catalog, schema, sample_mod)
src/sql/quality/          data quality gates
tests/                    local CI tests
report/                   project report (EN, FR)
```

## Limits

The dataset's origin is unverified and covers four months. There is no acquisition cost, so channel profitability is gross. The risk score combines simple signals not validated against confirmed fraud. Details in the report.
