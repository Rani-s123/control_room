-- ---------------------------------------------------------------------------
-- EYEWITNESS (corroboration): what errors did the culprit slice actually throw,
-- and how damaged was the decode?
--
-- The session drill-down above cannot answer this. Errors are about one event
-- in a hundred, so the twenty worst-stalling sessions routinely contain no
-- error rows at all, and a fault domain decided from that sample is decided by
-- luck: a packaging fault with no MANIFEST_404 in the sample got paged to the
-- CDN team. This aggregates over the whole slice instead, so the dominant code
-- is a rate rather than a coin flip.
-- Params: {dim:Identifier}, {value:String}, {window_min:UInt32}
-- ---------------------------------------------------------------------------
SELECT
    error_code                                              AS error_code,
    count()                                                 AS n,
    round(count() / greatest(sum(count()) OVER (), 1), 3)   AS share
FROM control_room.playback_events
WHERE ts >= now() - INTERVAL {window_min:UInt32} MINUTE
  AND {dim:Identifier} = {value:String}
  AND event_type = 'error'
  AND error_code != ''
GROUP BY error_code
ORDER BY n DESC
LIMIT 5;
