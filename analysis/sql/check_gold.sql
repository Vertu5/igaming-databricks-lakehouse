SELECT 'channel' AS view, acquisition_channel AS k, CAST(registered_players AS STRING) AS a, CAST(active_players AS STRING) AS b, CAST(activation_rate AS STRING) AS c, CAST(ggr AS STRING) AS d, CAST(ggr_per_registered_player AS STRING) AS e, CAST(hold_rate AS STRING) AS f
FROM workspace.igaming.gold_channel_performance
UNION ALL
SELECT 'segment', segment, CAST(COUNT(*) AS STRING), CAST(ROUND(SUM(turnover), 0) AS STRING), CAST(ROUND(SUM(turnover) / (SELECT SUM(turnover) FROM workspace.igaming.gold_player_value), 4) AS STRING), CAST(ROUND(SUM(ggr), 0) AS STRING), CAST(ROUND(SUM(ggr) / (SELECT SUM(ggr) FROM workspace.igaming.gold_player_value), 4) AS STRING), ''
FROM workspace.igaming.gold_player_value GROUP BY segment
UNION ALL
SELECT 'risk', risk_level, CAST(COUNT(*) AS STRING), CAST(COUNT(DISTINCT player_id) AS STRING), CAST(ROUND(COUNT(*) / (SELECT COUNT(*) FROM workspace.igaming.gold_risk_sessions), 4) AS STRING), '', '', ''
FROM workspace.igaming.gold_risk_sessions GROUP BY risk_level
ORDER BY 1, 6 DESC
