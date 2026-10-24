-- Parameters (set by the job): catalog, schema.
USE CATALOG IDENTIFIER(:catalog);
USE SCHEMA IDENTIFIER(:schema);

-- Gold layer: business-ready tables answering concrete questions.

-- 1) Marketing: which acquisition channel is the most profitable?
-- All registered players are counted (LEFT JOIN) to expose the activation rate.
CREATE OR REPLACE TABLE gold_channel_performance AS
WITH activity AS (
    SELECT
        player_id,
        COUNT(*) AS spins,
        SUM(bet_amount) AS turnover,
        SUM(net_revenue) AS ggr
    FROM silver_player_activity
    GROUP BY player_id
)
SELECT
    p.acquisition_channel,
    COUNT(*) AS registered_players,
    COUNT(a.player_id) AS active_players,
    ROUND(COUNT(a.player_id) / COUNT(*), 4) AS activation_rate,
    SUM(COALESCE(a.spins, 0)) AS spins,
    ROUND(SUM(COALESCE(a.turnover, 0)), 2) AS turnover,
    ROUND(SUM(COALESCE(a.ggr, 0)), 2) AS ggr,
    ROUND(SUM(COALESCE(a.ggr, 0)) / COUNT(*), 2) AS ggr_per_registered_player,
    ROUND(SUM(COALESCE(a.ggr, 0)) / NULLIF(COUNT(a.player_id), 0), 2) AS ggr_per_active_player,
    ROUND(SUM(COALESCE(a.ggr, 0)) / NULLIF(SUM(a.turnover), 0), 4) AS hold_rate
FROM bronze_players p
LEFT JOIN activity a ON p.player_id = a.player_id
GROUP BY p.acquisition_channel;

-- 2) Player value: RFM scoring and segments (VIP = top 1 percent of turnover).
CREATE OR REPLACE TABLE gold_player_value AS
WITH spin_agg AS (
    SELECT
        player_id,
        COUNT(*) AS spins,
        COUNT(DISTINCT session_id) AS sessions,
        COUNT(DISTINCT CAST(ts AS DATE)) AS active_days,
        SUM(bet_amount) AS turnover,
        SUM(win_amount) AS total_wins,
        SUM(bet_amount - win_amount) AS ggr,
        AVG(bet_amount) AS avg_bet,
        MAX(bet_amount) AS max_bet,
        SUM(CASE WHEN feature_buy = 1 THEN 1 ELSE 0 END) AS feature_buys,
        MIN(CAST(ts AS DATE)) AS first_play_date,
        MAX(CAST(ts AS DATE)) AS last_play_date
    FROM bronze_spins
    WHERE bet_amount > 0
    GROUP BY player_id
),
ref AS (
    SELECT MAX(last_play_date) AS ref_date FROM spin_agg
),
base AS (
    SELECT s.*, DATEDIFF(r.ref_date, s.last_play_date) AS recency_days
    FROM spin_agg s CROSS JOIN ref r
),
scored AS (
    SELECT
        *,
        NTILE(5) OVER (ORDER BY recency_days DESC) AS r_score,
        NTILE(5) OVER (ORDER BY sessions ASC) AS f_score,
        NTILE(5) OVER (ORDER BY turnover ASC) AS m_score,
        PERCENT_RANK() OVER (ORDER BY turnover ASC) AS turnover_pct
    FROM base
)
SELECT
    player_id,
    spins,
    sessions,
    active_days,
    ROUND(turnover, 2) AS turnover,
    ROUND(total_wins, 2) AS total_wins,
    ROUND(ggr, 2) AS ggr,
    ROUND(avg_bet, 2) AS avg_bet,
    ROUND(max_bet, 2) AS max_bet,
    feature_buys,
    first_play_date,
    last_play_date,
    recency_days,
    r_score,
    f_score,
    m_score,
    ROUND(turnover_pct, 4) AS turnover_pct,
    CASE
        WHEN turnover_pct >= 0.99 THEN 'VIP'
        WHEN r_score >= 4 AND f_score >= 4 THEN 'Champion'
        WHEN f_score >= 4 THEN 'Loyal'
        WHEN r_score <= 2 AND m_score >= 4 THEN 'At risk (high value)'
        WHEN r_score <= 2 THEN 'Dormant'
        ELSE 'Casual'
    END AS segment
FROM scored;

-- 3) Risk: session-level flags based on data-driven thresholds (99th percentiles),
-- geo mismatch, and devices or IPs shared by several players.
CREATE OR REPLACE TABLE gold_risk_sessions AS
WITH thr AS (
    SELECT
        percentile_approx(duration_sec, 0.99) AS dur_p99,
        percentile_approx(spins_per_min, 0.99) AS spm_p99,
        percentile_approx(max_bet, 0.99) AS maxbet_p99
    FROM bronze_sessions
),
shared_dev AS (
    SELECT device_id_hash FROM bronze_sessions
    GROUP BY device_id_hash HAVING COUNT(DISTINCT player_id) > 1
),
shared_ip AS (
    SELECT ip_hash FROM bronze_sessions
    GROUP BY ip_hash HAVING COUNT(DISTINCT player_id) > 1
),
flagged AS (
    SELECT
        s.session_id,
        s.player_id,
        s.start_ts,
        s.duration_sec,
        s.spins_per_min,
        s.max_bet,
        s.num_spins,
        s.ip_country,
        pl.country AS declared_country,
        s.geo_mismatch,
        CASE WHEN s.duration_sec >= t.dur_p99 THEN 1 ELSE 0 END AS flag_long_session,
        CASE WHEN s.spins_per_min >= t.spm_p99 THEN 1 ELSE 0 END AS flag_high_pace,
        CASE WHEN s.max_bet >= t.maxbet_p99 THEN 1 ELSE 0 END AS flag_high_bet,
        CASE WHEN d.device_id_hash IS NOT NULL THEN 1 ELSE 0 END AS flag_shared_device,
        CASE WHEN i.ip_hash IS NOT NULL THEN 1 ELSE 0 END AS flag_shared_ip
    FROM bronze_sessions s
    CROSS JOIN thr t
    LEFT JOIN bronze_players pl ON s.player_id = pl.player_id
    LEFT JOIN shared_dev d ON s.device_id_hash = d.device_id_hash
    LEFT JOIN shared_ip i ON s.ip_hash = i.ip_hash
)
SELECT
    *,
    (geo_mismatch + flag_long_session + flag_high_pace + flag_high_bet + flag_shared_device + flag_shared_ip) AS risk_score,
    CASE
        WHEN (geo_mismatch + flag_long_session + flag_high_pace + flag_high_bet + flag_shared_device + flag_shared_ip) >= 3 THEN 'High'
        WHEN (geo_mismatch + flag_long_session + flag_high_pace + flag_high_bet + flag_shared_device + flag_shared_ip) = 2 THEN 'Medium'
        WHEN (geo_mismatch + flag_long_session + flag_high_pace + flag_high_bet + flag_shared_device + flag_shared_ip) = 1 THEN 'Low'
        ELSE 'None'
    END AS risk_level
FROM flagged;

-- 4) Wide player table: single source for Text-to-SQL and the portfolio.
CREATE OR REPLACE TABLE gold_player_stats AS
WITH risk AS (
    SELECT
        player_id,
        COUNT(*) AS risk_sessions_total,
        SUM(CASE WHEN risk_level IN ('High', 'Medium') THEN 1 ELSE 0 END) AS risky_sessions,
        MAX(risk_score) AS max_risk_score
    FROM gold_risk_sessions
    GROUP BY player_id
)
SELECT
    p.player_id,
    p.signup_ts,
    p.country,
    p.acquisition_channel,
    p.device_pref,
    p.os,
    COALESCE(v.segment, 'No activity') AS segment,
    COALESCE(v.sessions, 0) AS sessions,
    COALESCE(v.spins, 0) AS spins,
    COALESCE(v.turnover, 0) AS turnover,
    COALESCE(v.ggr, 0) AS ggr,
    v.avg_bet,
    COALESCE(v.feature_buys, 0) AS feature_buys,
    v.last_play_date,
    v.recency_days,
    COALESCE(r.risky_sessions, 0) AS risky_sessions,
    COALESCE(r.max_risk_score, 0) AS max_risk_score
FROM bronze_players p
LEFT JOIN gold_player_value v ON p.player_id = v.player_id
LEFT JOIN risk r ON p.player_id = r.player_id;
