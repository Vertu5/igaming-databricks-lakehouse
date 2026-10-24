-- Data quality gate after Silver.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

SELECT assert_true((SELECT COUNT(*) FROM silver_player_activity) > 0, 'silver is empty');
SELECT assert_true((SELECT COUNT(*) - COUNT(DISTINCT spin_id) FROM silver_player_activity) = 0, 'silver contains duplicate spins (join fan-out)');
SELECT assert_true((SELECT COUNT(*) FROM silver_player_activity WHERE player_id IS NULL OR session_id IS NULL OR spin_id IS NULL) = 0, 'null keys in silver');
SELECT assert_true((SELECT COUNT(*) FROM silver_player_activity WHERE bet_amount <= 0) = 0, 'non-positive bets survived cleaning');
SELECT assert_true((SELECT COUNT(*) FROM silver_player_activity WHERE ABS(net_revenue - (bet_amount - win_amount)) > 0.000001) = 0, 'net_revenue is inconsistent');
-- No spin may be lost by the joins: every valid bronze spin must be in silver.
SELECT assert_true(
    (SELECT COUNT(*) FROM silver_player_activity) = (SELECT COUNT(*) FROM bronze_spins WHERE bet_amount > 0),
    'spins were lost between bronze and silver');
