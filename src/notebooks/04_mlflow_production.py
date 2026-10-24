# Databricks notebook source
# MAGIC %md
# MAGIC # 04 — End-to-End MLOps on Databricks: MLflow Tracking, Unity Catalog Registry & Batch Scoring
# MAGIC
# MAGIC This notebook industrializes the Data Science layer directly inside the **Databricks Lakehouse**:
# MAGIC 1. **Leakage-Free Feature Windowing**: Builds historical features on `[min_date, max_date - 30d]` from `bronze_spins` and labels 30-day churn on `(max_date - 30d, max_date]`.
# MAGIC 2. **MLflow Experiment Tracking**:
# MAGIC    - Compares **Logistic Regression** vs. **Gradient Boosting** for 30-day churn prediction (logging hyperparameters, 5-fold CV AUC, held-out `test_roc_auc`, `test_pr_auc`, `test_brier_score`, and MLflow model signatures).
# MAGIC    - Tracks **K-Means ($k=4$)** player behavioral segmentation (`silhouette_score`) and **Isolation Forest** session anomaly detection.
# MAGIC 3. **Unity Catalog Model Registry (`@champion`)**: Registers the winning model in `${catalog}.${schema}.igaming_churn_model` and assigns the `@champion` alias.
# MAGIC 4. **Production Batch Inference**: Loads `models:/${catalog}.${schema}.igaming_churn_model@champion` from Unity Catalog and writes actionable CRM scores (`churn_probability`, `churn_risk_tier`, `cluster_persona`, `expected_ggr_at_risk`) to `${catalog}.${schema}.gold_player_ml_scores`.

# COMMAND ----------

import mlflow
import mlflow.sklearn
from mlflow.models import infer_signature
from mlflow.tracking import MlflowClient
import numpy as np
import pandas as pd
from pyspark.sql import functions as F
from sklearn.cluster import KMeans
from sklearn.ensemble import GradientBoostingClassifier, IsolationForest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    f1_score,
    roc_auc_score,
    silhouette_score,
)
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

dbutils.widgets.text("catalog", "workspace", "Unity Catalog")
dbutils.widgets.text("schema", "igaming", "Target Schema")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
SEED = 42

print(f"Running MLOps pipeline on target: {catalog}.{schema}")

mlflow.set_registry_uri("databricks-uc")
current_user = spark.sql("SELECT current_user() AS u").collect()[0]["u"]
experiment_path = f"/Users/{current_user}/igaming_mlflow_{schema}"
mlflow.set_experiment(experiment_path)
print(f"MLflow Experiment: {experiment_path}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Temporal Feature Windowing (Zero Target Leakage)
# MAGIC Features are computed strictly prior to the 30-day observation cutoff (`max_d - 30 days`), while the binary label `churned` measures whether a previously active player had zero activity in the final 30 days.

# COMMAND ----------

churn_query = f"""
WITH bounds AS (
    SELECT MAX(CAST(ts AS DATE)) AS max_d
    FROM {catalog}.{schema}.bronze_spins
),
feat AS (
    SELECT
        s.player_id,
        COUNT(*) AS spins,
        COUNT(DISTINCT s.session_id) AS sessions,
        COUNT(DISTINCT CAST(s.ts AS DATE)) AS active_days,
        SUM(s.bet_amount) AS turnover,
        AVG(s.bet_amount) AS avg_bet,
        SUM(CASE WHEN s.feature_buy = 1 THEN 1 ELSE 0 END) AS feature_buys,
        DATEDIFF(MAX(CAST(s.ts AS DATE)), MIN(CAST(s.ts AS DATE))) AS tenure_days,
        DATEDIFF(DATE_SUB((SELECT max_d FROM bounds), 30), MAX(CAST(s.ts AS DATE))) AS days_idle_before_cutoff
    FROM {catalog}.{schema}.bronze_spins s
    CROSS JOIN bounds b
    WHERE CAST(s.ts AS DATE) <= DATE_SUB(b.max_d, 30)
      AND s.bet_amount > 0
    GROUP BY s.player_id
),
label AS (
    SELECT DISTINCT player_id
    FROM {catalog}.{schema}.bronze_spins s
    CROSS JOIN bounds b
    WHERE CAST(s.ts AS DATE) > DATE_SUB(b.max_d, 30)
)
SELECT
    f.*,
    CASE WHEN l.player_id IS NULL THEN 1 ELSE 0 END AS churned
FROM feat f
LEFT JOIN label l ON f.player_id = l.player_id
"""

df_churn = spark.sql(churn_query).toPandas()
feature_cols = [
    "spins",
    "sessions",
    "active_days",
    "turnover",
    "avg_bet",
    "feature_buys",
    "tenure_days",
    "days_idle_before_cutoff",
]
log_cols = ["spins", "sessions", "turnover", "avg_bet", "feature_buys"]

for col in feature_cols:
    df_churn[col] = pd.to_numeric(df_churn[col], errors="coerce").fillna(0.0)

X = df_churn[feature_cols].copy()
for c in log_cols:
    X[c] = np.log1p(np.maximum(X[c], 0.0))
y = df_churn["churned"].astype(int)

X_train, X_test, y_train, y_test = train_test_split(
    X, y, test_size=0.30, random_state=SEED, stratify=y
)
print(
    f"Cohort size: {len(df_churn)} active pre-cutoff players | "
    f"30d Churn rate: {y.mean():.2%}"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. MLflow Experiment Tracking: Challenger vs. Champion Churn Models
# MAGIC We train and evaluate two candidate architectures (`LogisticRegression` and `GradientBoostingClassifier`) and log parameters, cross-validation metrics, held-out test metrics, and input/output signatures to MLflow.

# COMMAND ----------

candidates = {
    "logistic_regression_baseline": Pipeline(
        [
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=1.0, max_iter=1000, random_state=SEED)),
        ]
    ),
    "gradient_boosting_challenger": Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "clf",
                GradientBoostingClassifier(
                    n_estimators=80,
                    max_depth=3,
                    learning_rate=0.05,
                    subsample=0.85,
                    random_state=SEED,
                ),
            ),
        ]
    ),
}

best_run_id = None
best_model_key = None
best_auc = -1.0

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=SEED)

for model_key, pipeline in candidates.items():
    with mlflow.start_run(run_name=f"churn_{model_key}") as run:
        pipeline.fit(X_train, y_train)
        y_prob = pipeline.predict_proba(X_test)[:, 1]
        y_pred = (y_prob >= 0.5).astype(int)

        test_roc_auc = float(roc_auc_score(y_test, y_prob))
        test_pr_auc = float(average_precision_score(y_test, y_prob))
        test_brier = float(brier_score_loss(y_test, y_prob))
        test_f1 = float(f1_score(y_test, y_pred))
        cv_auc = float(
            cross_val_score(pipeline, X_train, y_train, cv=cv, scoring="roc_auc").mean()
        )

        mlflow.log_params(
            {
                "model_family": model_key,
                "catalog": catalog,
                "schema": schema,
                "n_features": len(feature_cols),
                "train_rows": len(X_train),
                "test_rows": len(X_test),
                "window_days": 30,
            }
        )
        mlflow.log_metrics(
            {
                "test_roc_auc": round(test_roc_auc, 4),
                "test_pr_auc": round(test_pr_auc, 4),
                "test_brier_score": round(test_brier, 4),
                "test_f1_score": round(test_f1, 4),
                "cv_roc_auc_mean": round(cv_auc, 4),
            }
        )

        signature = infer_signature(
            X_train.head(5), pipeline.predict_proba(X_train.head(5))[:, 1]
        )
        mlflow.sklearn.log_model(
            sk_model=pipeline,
            artifact_path="model",
            signature=signature,
            input_example=X_train.head(3),
        )

        print(
            f"[{model_key}] run_id={run.info.run_id} | "
            f"test_roc_auc={test_roc_auc:.4f} | cv_roc_auc={cv_auc:.4f} | brier={test_brier:.4f}"
        )

        if test_roc_auc > best_auc:
            best_auc = test_roc_auc
            best_run_id = run.info.run_id
            best_model_key = model_key

print(
    f"Selected Champion Model: {best_model_key} "
    f"(test_roc_auc={best_auc:.4f}, run_id={best_run_id})"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Register Champion Model in Unity Catalog (`@champion` Alias)
# MAGIC We register the winning MLflow run into Unity Catalog Model Registry (`${catalog}.${schema}.igaming_churn_model`) and assign the `@champion` alias for production governance.

# COMMAND ----------

registered_model_name = f"{catalog}.{schema}.igaming_churn_model"
model_uri = f"runs:/{best_run_id}/model"

registered_version = mlflow.register_model(
    model_uri=model_uri,
    name=registered_model_name,
)

client = MlflowClient()
client.set_registered_model_alias(
    name=registered_model_name,
    alias="champion",
    version=registered_version.version,
)

print(
    f"Registered {registered_model_name} version {registered_version.version} "
    f"with alias '@champion'"
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. K-Means Behavioral Segmentation, Isolation Forest & Production Batch Scoring (`gold_player_ml_scores`)
# MAGIC We load the `@champion` model directly from Unity Catalog (`models:/.../igaming_churn_model@champion`), combine it with K-Means ($k=4$) behavioral clustering on `gold_player_value`, log an Isolation Forest anomaly detection run on `gold_risk_sessions`, and write the enriched CRM action table `${catalog}.${schema}.gold_player_ml_scores`.

# COMMAND ----------

# 4a. Track Isolation Forest Anomaly Detection on gold_risk_sessions
df_risk = spark.sql(
    f"""
    SELECT session_id, player_id, duration_sec, spins_per_min, max_bet, num_spins,
           risk_score, risk_level
    FROM {catalog}.{schema}.gold_risk_sessions
"""
).toPandas()

risk_feats = ["duration_sec", "spins_per_min", "max_bet", "num_spins"]
X_risk = df_risk[risk_feats].apply(pd.to_numeric, errors="coerce").fillna(0.0)
for c in risk_feats:
    X_risk[c] = np.log1p(np.maximum(X_risk[c], 0.0))

with mlflow.start_run(run_name="isolation_forest_session_anomalies"):
    iso = IsolationForest(n_estimators=200, contamination=0.01, random_state=SEED).fit(
        X_risk
    )
    is_anom = (iso.predict(X_risk) == -1).astype(int)
    rules_flag = df_risk["risk_level"].isin(["High", "Medium"]).astype(int)
    overlap = int(((is_anom == 1) & (rules_flag == 1)).sum())
    mlflow.log_params({"n_estimators": 200, "contamination": 0.01})
    mlflow.log_metrics(
        {
            "total_sessions": len(df_risk),
            "ml_anomalies": int(is_anom.sum()),
            "rule_high_medium": int(rules_flag.sum()),
            "rule_ml_overlap": overlap,
        }
    )

# 4b. Load active players from gold_player_value + gold_player_stats
df_active = spark.sql(
    f"""
    SELECT
        v.player_id,
        s.country,
        s.acquisition_channel,
        v.segment AS rfm_segment,
        v.spins,
        v.sessions,
        v.active_days,
        v.turnover,
        v.ggr,
        v.avg_bet,
        v.max_bet,
        v.feature_buys,
        DATEDIFF(v.last_play_date, v.first_play_date) AS tenure_days,
        v.recency_days,
        s.risky_sessions,
        s.max_risk_score
    FROM {catalog}.{schema}.gold_player_value v
    INNER JOIN {catalog}.{schema}.gold_player_stats s
        ON v.player_id = s.player_id
"""
).toPandas()

for col in [
    "spins",
    "sessions",
    "active_days",
    "turnover",
    "ggr",
    "avg_bet",
    "max_bet",
    "feature_buys",
    "tenure_days",
    "recency_days",
    "risky_sessions",
    "max_risk_score",
]:
    df_active[col] = pd.to_numeric(df_active[col], errors="coerce").fillna(0.0)

# K-Means (k=4) behavioral clustering logged in MLflow
cluster_feats = [
    "sessions",
    "active_days",
    "turnover",
    "avg_bet",
    "max_bet",
    "feature_buys",
    "recency_days",
]
X_cluster = df_active[cluster_feats].copy()
for c in ["sessions", "turnover", "avg_bet", "max_bet", "feature_buys"]:
    X_cluster[c] = np.log1p(np.maximum(X_cluster[c], 0.0))
X_cluster_scaled = StandardScaler().fit_transform(X_cluster)

with mlflow.start_run(run_name="kmeans_player_segmentation_k4"):
    kmeans = KMeans(n_clusters=4, n_init=10, random_state=SEED)
    raw_labels = kmeans.fit_predict(X_cluster_scaled)
    sil_score = float(silhouette_score(X_cluster_scaled, raw_labels))
    mlflow.log_params({"n_clusters": 4, "features": ",".join(cluster_feats)})
    mlflow.log_metric("silhouette_score", round(sil_score, 4))

df_active["raw_cluster"] = raw_labels
order = (
    df_active.groupby("raw_cluster")["turnover"].mean().sort_values().index.tolist()
)
rank_map = {old_idx: new_idx for new_idx, old_idx in enumerate(order)}
persona_names = {
    0: "Casual Low-Stakes",
    1: "Regular Multi-Game",
    2: "High-Frequency Grinders",
    3: "VIP High-Rollers",
}
df_active["cluster_id"] = df_active["raw_cluster"].map(rank_map).astype(int)
df_active["cluster_persona"] = df_active["cluster_id"].map(persona_names)

# Load @champion churn model from Unity Catalog Registry and score all active players
champion_model = mlflow.sklearn.load_model(f"models:/{registered_model_name}@champion")

X_score = pd.DataFrame(
    {
        "spins": np.log1p(np.maximum(df_active["spins"], 0.0)),
        "sessions": np.log1p(np.maximum(df_active["sessions"], 0.0)),
        "active_days": df_active["active_days"],
        "turnover": np.log1p(np.maximum(df_active["turnover"], 0.0)),
        "avg_bet": np.log1p(np.maximum(df_active["avg_bet"], 0.0)),
        "feature_buys": np.log1p(np.maximum(df_active["feature_buys"], 0.0)),
        "tenure_days": df_active["tenure_days"],
        "days_idle_before_cutoff": df_active["recency_days"],
    }
)

probs = champion_model.predict_proba(X_score)[:, 1]
df_active["churn_probability"] = np.round(probs, 4)
df_active["churn_risk_tier"] = np.where(
    df_active["churn_probability"] >= 0.65,
    "High Risk",
    np.where(df_active["churn_probability"] >= 0.40, "Medium Risk", "Low Risk"),
)
df_active["expected_ggr_at_risk"] = np.round(
    np.maximum(df_active["ggr"], 0.0) * df_active["churn_probability"], 2
)
df_active["model_registry_uri"] = (
    f"{registered_model_name}@champion (v{registered_version.version})"
)

output_cols = [
    "player_id",
    "country",
    "acquisition_channel",
    "rfm_segment",
    "cluster_id",
    "cluster_persona",
    "recency_days",
    "churn_probability",
    "churn_risk_tier",
    "turnover",
    "ggr",
    "expected_ggr_at_risk",
    "risky_sessions",
    "model_registry_uri",
]

spark_out = spark.createDataFrame(df_active[output_cols]).withColumn(
    "scored_at", F.current_timestamp()
)
target_table = f"{catalog}.{schema}.gold_player_ml_scores"
spark_out.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    target_table
)

print(f"Successfully wrote {len(df_active)} scored players to {target_table}")
