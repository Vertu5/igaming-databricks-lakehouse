SELECT
    COUNT(*) AS n_sessions,
    ROUND(percentile_approx(duration_sec, 0.5)) AS dur_p50,
    ROUND(percentile_approx(duration_sec, 0.95)) AS dur_p95,
    ROUND(percentile_approx(duration_sec, 0.99)) AS dur_p99,
    ROUND(percentile_approx(spins_per_min, 0.5), 2) AS spm_p50,
    ROUND(percentile_approx(spins_per_min, 0.99), 2) AS spm_p99,
    ROUND(MAX(spins_per_min), 2) AS spm_max,
    ROUND(percentile_approx(max_bet, 0.99), 2) AS maxbet_p99,
    ROUND(AVG(geo_mismatch), 4) AS geo_mismatch_rate,
    COUNT(DISTINCT device_id_hash) AS devices,
    COUNT(DISTINCT ip_hash) AS ips
FROM workspace.igaming.bronze_sessions
