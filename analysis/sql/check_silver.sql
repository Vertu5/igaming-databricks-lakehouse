SELECT
    (SELECT COUNT(*) FROM workspace.igaming.bronze_spins) AS bronze_spins,
    (SELECT COUNT(*) FROM workspace.igaming.bronze_spins WHERE bet_amount <= 0 OR bet_amount IS NULL) AS dropped_bad_bets,
    COUNT(*) AS silver_rows,
    COUNT(DISTINCT player_id) AS players,
    COUNT(DISTINCT session_id) AS sessions,
    ROUND(SUM(net_revenue), 2) AS total_ggr,
    ROUND(MAX(duration_sec) / 3600, 2) AS max_session_hours,
    SUM(CASE WHEN geo_mismatch = 1 THEN 1 ELSE 0 END) AS geo_mismatch_spins
FROM workspace.igaming.silver_player_activity
