-- Data quality gate after MLflow batch scoring: verify gold_player_ml_scores integrity.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- 1) Every active player in gold_player_value must be scored exactly once.
SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_ml_scores) = (SELECT COUNT(*) FROM gold_player_value),
    'gold_player_ml_scores row count does not match gold_player_value');

SELECT assert_true(
    (SELECT COUNT(*) - COUNT(DISTINCT player_id) FROM gold_player_ml_scores) = 0,
    'duplicate player_id in gold_player_ml_scores');

-- 2) Probabilities must be valid in [0, 1] and non-null.
SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_ml_scores WHERE churn_probability IS NULL OR churn_probability NOT BETWEEN 0.0 AND 1.0) = 0,
    'invalid churn_probability out of [0, 1] in gold_player_ml_scores');

-- 3) Risk tiers and K-Means personas must be populated from valid domains.
SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_ml_scores WHERE churn_risk_tier NOT IN ('Low Risk', 'Medium Risk', 'High Risk')) = 0,
    'invalid churn_risk_tier in gold_player_ml_scores');

SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_ml_scores WHERE cluster_persona IS NULL OR cluster_id NOT BETWEEN 0 AND 3) = 0,
    'invalid cluster assignment in gold_player_ml_scores');

-- 4) Expected GGR at risk must be non-negative.
SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_ml_scores WHERE expected_ggr_at_risk < 0) = 0,
    'negative expected_ggr_at_risk in gold_player_ml_scores');
