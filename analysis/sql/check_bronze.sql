SELECT 'players' AS tbl, COUNT(*) AS n FROM workspace.igaming.bronze_players
UNION ALL SELECT 'sessions', COUNT(*) FROM workspace.igaming.bronze_sessions
UNION ALL SELECT 'spins', COUNT(*) FROM workspace.igaming.bronze_spins
