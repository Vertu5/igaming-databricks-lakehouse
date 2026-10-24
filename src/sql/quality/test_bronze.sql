-- Data quality gate after Bronze. Any violated rule makes assert_true raise, which fails the job task.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

SELECT assert_true((SELECT COUNT(*) FROM bronze_players) > 0, 'bronze_players is empty');
SELECT assert_true((SELECT COUNT(*) FROM bronze_sessions) > 0, 'bronze_sessions is empty');
SELECT assert_true((SELECT COUNT(*) FROM bronze_spins) > 0, 'bronze_spins is empty');
SELECT assert_true((SELECT COUNT(*) - COUNT(DISTINCT player_id) FROM bronze_players) = 0, 'duplicate player_id');
SELECT assert_true((SELECT COUNT(*) - COUNT(DISTINCT spin_id) FROM bronze_spins) = 0, 'duplicate spin_id');
SELECT assert_true((SELECT COUNT(*) FROM bronze_spins WHERE _rescued_data IS NOT NULL) = 0, 'CSV values that could not be parsed');
SELECT assert_true((SELECT COUNT(*) FROM bronze_spins s LEFT ANTI JOIN bronze_sessions x ON s.session_id = x.session_id) = 0, 'spins without a session');
