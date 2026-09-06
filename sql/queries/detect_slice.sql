-- WATCHER (slice sweep): the same fixed z-score as detect.sql, but computed
-- for every slice of one dimension against that slice's OWN baseline.
--
-- Why this exists. A fault contained in one slice barely moves the global
-- per-minute average: an encoder emitting corrupt slices on a single rendition
-- hurts a fifth of the audience while lifting the all-up number by a few
-- percent, which is inside normal variance. Detection on the global series
-- alone therefore drops real incidents before anything else in the pipeline
-- gets to look at them — the exact blind spot in the QoE dashboards this
-- system exists to replace, reproduced in step 1.
--
-- Slices below {min_sessions:UInt32} average sessions are excluded. A slice
-- with a handful of sessions per minute has a tiny sigma and will produce a
-- spectacular z-score from one unlucky viewer.
--
-- Params: {dim:Identifier}, {lookback_min:UInt32}, {baseline_min:UInt32}
WITH
    per_minute AS (
        SELECT
            minute                                                     AS m,
            {dim:Identifier}                                           AS slice,
            uniqMerge(sessions)                                        AS n_sessions,
            sumMerge(rebuffer_ms) / greatest(uniqMerge(sessions), 1)   AS rb_per_session
        FROM control_room.qoe_1m
        WHERE minute >= now() - INTERVAL {baseline_min:UInt32} MINUTE
        GROUP BY minute, slice
    ),
    baseline AS (
        SELECT
            slice                     AS slice,
            avg(rb_per_session)       AS mu,
            stddevPop(rb_per_session) AS sigma,
            avg(n_sessions)           AS avg_sessions
        FROM per_minute
        WHERE m < now() - INTERVAL {lookback_min:UInt32} MINUTE
        GROUP BY slice
    )
SELECT
    p.slice                                                              AS slice,
    round(max((p.rb_per_session - b.mu) / greatest(b.sigma, 1)), 2)      AS peak_z,
    round(avg((p.rb_per_session - b.mu) / greatest(b.sigma, 1)), 2)      AS mean_z,
    -- Share of the window spent above baseline was tried here as a second
    -- statistic, on the theory that persistence would catch small slices whose
    -- noisy per-minute average inflates sigma. It does not separate: innocent
    -- slices sit at 0.85 to 1.00 during any real incident, because a genuine
    -- fault drags neighbouring slices up with it. Left out rather than left in
    -- looking useful.
    round(any(b.mu), 1)                                                  AS baseline_mu,
    round(max(p.rb_per_session), 1)                                      AS worst_minute,
    round(any(b.avg_sessions), 0)                                        AS avg_sessions
FROM per_minute p
INNER JOIN baseline b ON p.slice = b.slice
WHERE p.m >= now() - INTERVAL {lookback_min:UInt32} MINUTE
  AND b.avg_sessions >= {min_sessions:UInt32}
GROUP BY p.slice
ORDER BY peak_z DESC
LIMIT 8;
