-- The Control Room — ClickHouse schema
-- Raw player telemetry for a live OTT service, plus rollups the agents query.

CREATE DATABASE IF NOT EXISTS control_room;

-- ---------------------------------------------------------------------------
-- Raw heartbeat / event stream. One row per player event.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_room.playback_events
(
    ts              DateTime64(3, 'UTC'),
    session_id      String,
    user_id         String,
    event_type      LowCardinality(String),   -- start | heartbeat | rebuffer | bitrate_switch | error | ad_start | ad_complete | exit
    content_id      LowCardinality(String),
    content_title   LowCardinality(String),
    is_live         UInt8,

    cdn             LowCardinality(String),   -- akamai | cloudfront | fastly | edgecast
    pop             LowCardinality(String),   -- edge POP code, e.g. BOM2
    isp             LowCardinality(String),
    asn             UInt32,

    country         LowCardinality(String),
    region          LowCardinality(String),

    device_type     LowCardinality(String),   -- smart_tv | mobile_ios | mobile_android | web | console
    os              LowCardinality(String),
    player_version  LowCardinality(String),

    rendition       LowCardinality(String),   -- 1080p60 | 1080p | 720p | 480p | 360p
    bitrate_kbps    UInt32,
    startup_ms      UInt32,                   -- non-zero only on `start`
    rebuffer_ms     UInt32,                   -- non-zero only on `rebuffer`
    dropped_frames  UInt32,
    buffer_health_ms UInt32,
    error_code      LowCardinality(String),

    subscription    LowCardinality(String),   -- free_ad | premium
    ad_revenue_usd  Float32                   -- realised revenue on ad_complete
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (toStartOfMinute(ts), cdn, region, device_type)
TTL toDateTime(ts) + INTERVAL 90 DAY
SETTINGS index_granularity = 8192;

-- ---------------------------------------------------------------------------
-- Per-minute QoE rollup. The Watcher polls this, not the raw table, so
-- detection stays sub-second even at billions of rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_room.qoe_1m
(
    minute          DateTime('UTC'),
    content_id      LowCardinality(String),
    cdn             LowCardinality(String),
    region          LowCardinality(String),
    device_type     LowCardinality(String),
    isp             LowCardinality(String),
    player_version  LowCardinality(String),
    rendition       LowCardinality(String),

    sessions        AggregateFunction(uniq, String),
    rebuffer_events AggregateFunction(sum, UInt64),
    rebuffer_ms     AggregateFunction(sum, UInt64),
    startup_ms      AggregateFunction(quantile(0.95), UInt32),
    bitrate_kbps    AggregateFunction(avg, UInt32),
    errors          AggregateFunction(sum, UInt64),
    ad_revenue_usd  AggregateFunction(sum, Float32)
)
ENGINE = AggregatingMergeTree
PARTITION BY toYYYYMMDD(minute)
ORDER BY (minute, content_id, cdn, region, device_type, isp, player_version, rendition);

CREATE MATERIALIZED VIEW IF NOT EXISTS control_room.qoe_1m_mv
TO control_room.qoe_1m AS
SELECT
    toStartOfMinute(ts)                                       AS minute,
    content_id,
    cdn,
    region,
    device_type,
    isp,
    player_version,
    rendition,
    uniqState(session_id)                                     AS sessions,
    sumState(toUInt64(event_type = 'rebuffer'))               AS rebuffer_events,
    sumState(toUInt64(rebuffer_ms))                           AS rebuffer_ms,
    quantileState(0.95)(startup_ms)                           AS startup_ms,
    avgState(bitrate_kbps)                                    AS bitrate_kbps,
    sumState(toUInt64(event_type = 'error'))                  AS errors,
    sumState(ad_revenue_usd)                                  AS ad_revenue_usd
FROM control_room.playback_events
GROUP BY minute, content_id, cdn, region, device_type, isp, player_version, rendition;

-- ---------------------------------------------------------------------------
-- Every agent step is written back here. This is what makes a run replayable
-- and what the Director's Timeline in the UI reads from.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS control_room.agent_runs
(
    run_id          String,
    step_no         UInt8,
    ts              DateTime64(3, 'UTC'),
    agent           LowCardinality(String),    -- watcher | diagnostician | eyewitness | impact | action
    action          String,
    sql_executed    String,
    rows_scanned    UInt64,
    latency_ms      UInt32,
    model           LowCardinality(String),
    tokens_in       UInt32,
    tokens_out      UInt32,
    finding         String,
    confidence      Float32
)
ENGINE = MergeTree
PARTITION BY toYYYYMMDD(ts)
ORDER BY (run_id, step_no);

CREATE TABLE IF NOT EXISTS control_room.incidents
(
    run_id          String,
    opened_at       DateTime64(3, 'UTC'),
    closed_at       Nullable(DateTime64(3, 'UTC')),
    severity        LowCardinality(String),
    culprit_dim     String,
    culprit_value   String,
    root_cause      String,
    sessions_hit    UInt64,
    revenue_at_risk Float64,
    remediation     String,
    status          LowCardinality(String)     -- open | mitigated | resolved
)
ENGINE = ReplacingMergeTree(opened_at)
ORDER BY run_id;
