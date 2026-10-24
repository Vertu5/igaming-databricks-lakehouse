-- Data quality gate after Gold: reconciliation with Silver and domain rules.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- Revenue must reconcile between layers (small tolerance for per-channel rounding).
SELECT assert_true(
    ABS((SELECT SUM(ggr) FROM gold_channel_performance) - (SELECT SUM(net_revenue) FROM silver_player_activity)) < 0.1,
    'gold_channel_performance GGR does not reconcile with silver');
SELECT assert_true(
    ABS((SELECT SUM(turnover) FROM gold_player_value) - (SELECT SUM(bet_amount) FROM silver_player_activity))
        < 0.001 * (SELECT SUM(bet_amount) FROM silver_player_activity),
    'gold_player_value turnover does not reconcile with silver');
-- Every registered player appears exactly once in the wide table.
SELECT assert_true(
    (SELECT COUNT(*) FROM gold_player_stats) = (SELECT COUNT(*) FROM bronze_players),
    'gold_player_stats does not contain every registered player once');
SELECT assert_true((SELECT COUNT(*) - COUNT(DISTINCT player_id) FROM gold_player_stats) = 0, 'duplicate player in gold_player_stats');
-- Risk rules.
SELECT assert_true((SELECT COUNT(*) FROM gold_risk_sessions WHERE risk_score NOT BETWEEN 0 AND 6) = 0, 'risk_score out of range');
SELECT assert_true((SELECT COUNT(*) FROM gold_risk_sessions WHERE risk_level NOT IN ('None', 'Low', 'Medium', 'High')) = 0, 'unknown risk_level');
SELECT assert_true((SELECT COUNT(*) FROM gold_risk_sessions) = (SELECT COUNT(*) FROM bronze_sessions), 'risk table must cover every session');
-- Segmentation rules.
SELECT assert_true((SELECT COUNT(*) FROM gold_player_value WHERE segment IS NULL) = 0, 'player without a segment');
SELECT assert_true((SELECT COUNT(*) FROM gold_player_value WHERE turnover_pct NOT BETWEEN 0 AND 1) = 0, 'turnover_pct out of range');
