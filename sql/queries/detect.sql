-- WATCHER: fixed-threshold + z-score anomaly detection on the 1-minute rollup.
-- No model writes this SQL. Detection must be reproducible run to run.
--
-- Note: aggregate-state columns are never re-used as output aliases here.
-- ClickHouse resolves the alias first and `sumMerge(<alias>)` then fails at
-- runtime with ILLEGAL_TYPE_OF_ARGUMENT. Caught by tests/test_sql.py.
--
-- Params: {lookback_min:UInt32}, {baseline_min:UInt32}, {z_threshold:Float32}
WITH
    per_minute AS (
        SELECT
            minute                                                     AS m,
            uniqMerge(sessions)                                        AS n_sessions,
            sumMerge(rebuffer_ms) / greatest(uniqMerge(sessions), 1)   AS rb_per_session,
            quantileMerge(0.95)(startup_ms)                            AS p95_startup,
            sumMerge(errors)                                           AS n_errors
        FROM control_room.qoe_1m
        WHERE minute >= now() - INTERVAL {baseline_min:UInt32} MINUTE
        GROUP BY minute
    ),
    baseline AS (
        SELECT
            avg(rb_per_session)       AS mu,
            stddevPop(rb_per_session) AS sigma
        FROM per_minute
        WHERE m < now() - INTERVAL {lookback_min:UInt32} MINUTE
    )
SELECT
    p.m                                                          AS minute,
    p.n_sessions                                                 AS sessions,
    round(p.rb_per_session, 1)                                   AS rebuffer_ms_per_session,
    round(b.mu, 1)                                               AS baseline_mu,
    round((p.rb_per_session - b.mu) / greatest(b.sigma, 1), 2)   AS z_score,
    round(p.p95_startup, 0)                                      AS p95_startup_ms,
    p.n_errors                                                   AS errors,
    (p.rb_per_session - b.mu) / greatest(b.sigma, 1) > {z_threshold:Float32} AS is_anomalous
FROM per_minute p
CROSS JOIN baseline b
WHERE p.m >= now() - INTERVAL {lookback_min:UInt32} MINUTE
ORDER BY minute DESC;
