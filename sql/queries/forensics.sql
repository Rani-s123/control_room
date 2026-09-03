-- DIAGNOSTICIAN (drill-down): reconstruct what a real viewer experienced inside
-- the culprit slice — the ABR ladder walk, every stall, every error code.
-- Params: {dim:Identifier}, {value:String}, {window_min:UInt32}
SELECT
    session_id,
    device_type,
    player_version,
    isp,
    groupArray(10)(rendition)          AS ladder_walk,
    countIf(event_type = 'rebuffer')   AS stalls,
    sum(rebuffer_ms)                   AS total_stall_ms,
    max(startup_ms)                    AS startup_ms,
    sum(dropped_frames)                AS dropped_frames,
    groupUniqArray(error_code)         AS error_codes,
    min(ts)                            AS first_seen,
    max(ts)                            AS last_seen
FROM control_room.playback_events
WHERE ts >= now() - INTERVAL {window_min:UInt32} MINUTE
  AND {dim:Identifier} = {value:String}
GROUP BY session_id, device_type, player_version, isp
HAVING stalls > 0
ORDER BY total_stall_ms DESC
LIMIT 20;

-- ---------------------------------------------------------------------------
-- IMPACT: what this costs. Sessions hit, watch-time lost, ad revenue forgone,
-- and the churn-risk cohort (premium subs with >3 stalls).
-- Params: {dim:Identifier}, {value:String}, {window_min:UInt32}
-- ---------------------------------------------------------------------------
SELECT
    uniq(session_id)                                                AS sessions_hit,
    uniq(user_id)                                                   AS viewers_hit,
    round(sum(rebuffer_ms) / 1000 / 60, 1)                          AS stall_minutes,
    countIf(event_type = 'exit' AND rebuffer_ms > 0)                AS rage_quits,
    round(sumIf(ad_revenue_usd, event_type = 'ad_complete'), 2)     AS ad_revenue_realised,
    uniqIf(user_id, subscription = 'premium')                       AS premium_viewers_hit
FROM control_room.playback_events
WHERE ts >= now() - INTERVAL {window_min:UInt32} MINUTE
  AND {dim:Identifier} = {value:String};
