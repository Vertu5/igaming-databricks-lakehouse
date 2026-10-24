SELECT
    (SELECT COUNT(*) FROM (SELECT device_id_hash FROM workspace.igaming.bronze_sessions GROUP BY device_id_hash HAVING COUNT(DISTINCT player_id) > 1)) AS devices_shared_by_multi_players,
    (SELECT COUNT(*) FROM (SELECT ip_hash FROM workspace.igaming.bronze_sessions GROUP BY ip_hash HAVING COUNT(DISTINCT player_id) > 1)) AS ips_shared_by_multi_players,
    (SELECT COUNT(DISTINCT player_id) FROM workspace.igaming.bronze_sessions) AS players_with_sessions,
    (SELECT COUNT(*) FROM workspace.igaming.bronze_spins WHERE feature_buy = 1) AS feature_buy_spins,
    (SELECT ROUND(SUM(win_amount) / SUM(bet_amount), 4) FROM workspace.igaming.bronze_spins) AS global_rtp,
    (SELECT MIN(ts) FROM workspace.igaming.bronze_spins) AS first_spin,
    (SELECT MAX(ts) FROM workspace.igaming.bronze_spins) AS last_spin
