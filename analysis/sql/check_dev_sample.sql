SELECT
    (SELECT COUNT(*) FROM workspace.igaming_dev.bronze_players) AS dev_players,
    (SELECT COUNT(*) FROM workspace.igaming_dev.bronze_sessions) AS dev_sessions,
    (SELECT COUNT(*) FROM workspace.igaming_dev.bronze_spins) AS dev_spins,
    (SELECT COUNT(*) FROM workspace.igaming_dev.gold_player_stats) AS dev_gold_players,
    ROUND((SELECT COUNT(*) FROM workspace.igaming_dev.bronze_spins) / (SELECT COUNT(*) FROM workspace.igaming.bronze_spins), 4) AS spin_share_vs_prod
