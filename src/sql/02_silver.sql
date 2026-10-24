-- Parameters (set by the job): catalog, schema.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- Silver: one clean table linking player, session and spin.
CREATE OR REPLACE TABLE silver_player_activity AS
SELECT
    p.player_id,
    p.acquisition_channel,
    p.country,
    p.device_pref,
    sess.session_id,
    sess.duration_sec,
    sess.geo_mismatch,
    s.spin_id,
    s.bet_amount,
    s.win_amount,
    (s.bet_amount - s.win_amount) AS net_revenue -- money the casino keeps
FROM bronze_players p
JOIN bronze_sessions sess ON p.player_id = sess.player_id
JOIN bronze_spins s ON sess.session_id = s.session_id
WHERE s.bet_amount > 0; -- drop null/negative bets (cleaning)
