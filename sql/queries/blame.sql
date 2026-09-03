-- DIAGNOSTICIAN: multi-dimensional attribution over one dimension.
--
-- Returns four competing signals per slice so the ranking is auditable:
--
--   rebuffer_ms_now   stall time per session right now. High for chronically
--                     bad slices that are nobody's fault.
--   delta             change vs the slice's own baseline. Ignores blast size.
--   excess_ms_owned   delta × sessions. Better, still fooled by cohorts that
--                     merely overlap the real cause.
--   explanatory_power share of all POSITIVE excess stall time in the incident
--                     that this one slice accounts for, against a forecast from
--                     its own baseline. This is the discriminator.
--
-- Why explanatory power settles it: if a bad player release is the cause, then
-- 100% of the excess lives inside that version, so EP ≈ 1. The device class
-- that suffers most is only a slice of the affected population, so its EP is a
-- fraction. Containment, not severity, identifies a root cause.
--
-- `surprise` is the Jensen-Shannon divergence between the slice's baseline and
-- incident share, kept as a tiebreak and shown to the model as evidence.
--
-- Params: {dim:Identifier}, {window_min:UInt32}, {baseline_min:UInt32}
WITH
    cur AS (
        SELECT
            {dim:Identifier}                                        AS slice,
            uniqMerge(sessions)                                     AS n_sessions,
            sumMerge(rebuffer_ms)                                   AS stall_now,
            sumMerge(rebuffer_ms) / greatest(uniqMerge(sessions), 1) AS rb_now,
            sumMerge(errors)                                        AS n_errors,
            avgMerge(bitrate_kbps)                                  AS bitrate_now
        FROM control_room.qoe_1m
        WHERE minute >= now() - INTERVAL {window_min:UInt32} MINUTE
        GROUP BY slice
    ),
    base AS (
        SELECT
            {dim:Identifier}                                        AS slice,
            sumMerge(rebuffer_ms)                                   AS stall_before,
            sumMerge(rebuffer_ms) / greatest(uniqMerge(sessions), 1) AS rb_before,
            avgMerge(bitrate_kbps)                                  AS bitrate_before
        FROM control_room.qoe_1m
        WHERE minute <  now() - INTERVAL {window_min:UInt32} MINUTE
          AND minute >= now() - INTERVAL {baseline_min:UInt32} MINUTE
        GROUP BY slice
    ),
    joined AS (
        SELECT
            c.slice                                     AS slice,
            c.n_sessions                                AS n_sessions,
            c.rb_now                                    AS rb_now,
            ifNull(b.rb_before, 0)                      AS rb_before,
            c.stall_now                                 AS stall_now,
            ifNull(b.stall_before, 0)                   AS stall_before,
            c.n_errors                                  AS n_errors,
            c.bitrate_now - ifNull(b.bitrate_before, 0) AS bitrate_delta
        FROM cur c LEFT JOIN base b ON c.slice = b.slice
    ),
    k AS (
        -- scales the baseline window down to the length of the incident window,
        -- giving a forecast for what stall time *should* have been.
        SELECT {window_min:UInt32} / greatest({baseline_min:UInt32} - {window_min:UInt32}, 1) AS k
    ),
    totals AS (
        -- Denominator is the sum of POSITIVE excess only. Slices that improved
        -- must not cancel out slices that got worse, or a single bad slice can
        -- come back as owning 500% of an incident.
        SELECT
            sum(greatest(j.stall_now - j.stall_before * k.k, 0)) AS excess_total,
            any(k.k)                                             AS k,
            sum(j.stall_now)                                     AS C,
            greatest(sum(j.stall_before), 1)                     AS B
        FROM joined j CROSS JOIN k
    )
SELECT
    j.slice                                                             AS slice,
    j.n_sessions                                                        AS sessions,
    round(j.rb_now, 1)                                                  AS rebuffer_ms_now,
    round(j.rb_before, 1)                                               AS rebuffer_ms_before,
    round(j.rb_now - j.rb_before, 1)                                    AS delta,
    round((j.rb_now - j.rb_before) * j.n_sessions, 0)                   AS excess_ms_owned,
    -- forecast for this slice, and the share of total unforecast excess it owns
    round(j.stall_before * t.k, 0)                                      AS stall_forecast,
    round(greatest(j.stall_now - j.stall_before * t.k, 0)
          / greatest(t.excess_total, 1), 4)                             AS explanatory_power,
    -- Jensen-Shannon divergence between baseline share and incident share
    round(
        0.5 * (
            (j.stall_before / t.B) * log(2 * greatest(j.stall_before / t.B, 1e-9)
                / greatest(j.stall_before / t.B + j.stall_now / t.C, 1e-9))
          + (j.stall_now / t.C) * log(2 * greatest(j.stall_now / t.C, 1e-9)
                / greatest(j.stall_before / t.B + j.stall_now / t.C, 1e-9))
        ), 5)                                                           AS surprise,
    round(j.bitrate_delta, 0)                                           AS bitrate_delta_kbps,
    j.n_errors                                                          AS errors
FROM joined j
CROSS JOIN totals t
ORDER BY explanatory_power DESC
LIMIT 12;
