-- R3S accounting current schema
-- This is the current schema definition used to initialize a new persistent DB.
-- Do not treat the historical recovery label "v7" as a project version.

PRAGMA journal_mode=WAL;
PRAGMA synchronous=FULL;

BEGIN;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    device_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    group_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS device_macs (
    mac TEXT PRIMARY KEY,
    device_id INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ip_assignments (
    ip TEXT PRIMARY KEY,
    mac TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);

-- Historical IP -> MAC observations. Unlike ip_assignments, this table keeps
-- each observed identity instead of only the latest MAC for an IP.
CREATE TABLE IF NOT EXISTS ip_mac_history (
    observed_at INTEGER NOT NULL,
    ip TEXT NOT NULL,
    mac TEXT NOT NULL,
    PRIMARY KEY (observed_at, ip, mac)
);

CREATE INDEX IF NOT EXISTS idx_ip_mac_history_ip_time
    ON ip_mac_history (ip, observed_at);

CREATE INDEX IF NOT EXISTS idx_ip_mac_history_mac_time
    ON ip_mac_history (mac, observed_at);

CREATE TABLE IF NOT EXISTS counter_epochs (
    epoch_id INTEGER PRIMARY KEY,
    started_at INTEGER NOT NULL,
    reason TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS counter_baselines (
    direction TEXT NOT NULL,
    ip TEXT NOT NULL,
    epoch_id INTEGER NOT NULL,
    packets INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    sampled_at INTEGER NOT NULL,
    PRIMARY KEY (direction, ip)
);

CREATE TABLE IF NOT EXISTS destination_counter_baselines (
    direction TEXT NOT NULL CHECK (direction IN ('rx','tx')),
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    epoch_id INTEGER NOT NULL,
    packets INTEGER NOT NULL,
    bytes INTEGER NOT NULL,
    sampled_at INTEGER NOT NULL,
    PRIMARY KEY (direction, client_ip, destination_ip)
);

CREATE TABLE IF NOT EXISTS usage_hourly (
    hour_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_start_utc, device_id)
);

CREATE TABLE IF NOT EXISTS usage_daily (
    local_date TEXT NOT NULL,
    device_id INTEGER NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (local_date, device_id)
);

CREATE TABLE IF NOT EXISTS unresolved_usage (
    bucket_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    ip TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bucket_start_utc, ip)
);

CREATE TABLE IF NOT EXISTS destination_usage_hourly (
    hour_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    device_id INTEGER,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (hour_start_utc, identity_key, destination_ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_destination_usage_date
    ON destination_usage_hourly (local_date, local_hour, device_id);

CREATE INDEX IF NOT EXISTS idx_destination_usage_client_dest
    ON destination_usage_hourly (client_ip, destination_ip, hour_start_utc);

-- New destination samples are recorded in 15-minute buckets. The legacy
-- hourly table above remains readable until its normal retention window
-- expires, so an upgrade does not discard historical destination usage.
CREATE TABLE IF NOT EXISTS destination_usage_quarterly (
    quarter_start_utc INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    local_minute INTEGER NOT NULL,
    identity_key TEXT NOT NULL,
    device_id INTEGER,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    rx_bytes INTEGER NOT NULL DEFAULT 0,
    tx_bytes INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (quarter_start_utc, identity_key, destination_ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_destination_usage_quarter_date
    ON destination_usage_quarterly (local_date, local_hour, local_minute, device_id);

CREATE INDEX IF NOT EXISTS idx_destination_usage_quarter_client_dest
    ON destination_usage_quarterly (client_ip, destination_ip, quarter_start_utc);

CREATE TABLE IF NOT EXISTS destination_domain_observations (
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    domain TEXT NOT NULL,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    query_count INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (client_ip, destination_ip, domain)
);

CREATE INDEX IF NOT EXISTS idx_destination_domains_last_seen
    ON destination_domain_observations (last_seen);

CREATE TABLE IF NOT EXISTS adguard_querylog_cursor (
    path TEXT PRIMARY KEY,
    inode INTEGER NOT NULL,
    offset INTEGER NOT NULL,
    pending TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS health_events (
    event_time INTEGER NOT NULL,
    severity TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_events_time
    ON health_events (event_time);

-- One aggregated router-resource row per local hour. CPU values are stored
-- as hundredths of a percent and load values as hundredths of the 1-minute
-- load average to avoid floating-point storage.
CREATE TABLE IF NOT EXISTS router_health_hourly (
    hour_start_utc INTEGER PRIMARY KEY,
    local_date TEXT NOT NULL,
    local_hour INTEGER NOT NULL,
    cpu_sum_x100 INTEGER NOT NULL DEFAULT 0,
    cpu_min_x100 INTEGER,
    cpu_max_x100 INTEGER,
    cpu_samples INTEGER NOT NULL DEFAULT 0,
    memory_used_sum_bytes INTEGER NOT NULL,
    memory_used_min_bytes INTEGER NOT NULL,
    memory_used_max_bytes INTEGER NOT NULL,
    memory_total_bytes INTEGER NOT NULL,
    load_sum_x100 INTEGER NOT NULL,
    load_min_x100 INTEGER NOT NULL,
    load_max_x100 INTEGER NOT NULL,
    sample_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS router_health_cpu_baseline (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    total_ticks INTEGER NOT NULL,
    idle_ticks INTEGER NOT NULL,
    sampled_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS router_health_alert_state (
    resource TEXT PRIMARY KEY CHECK (resource IN ('cpu','memory')),
    high_since INTEGER,
    last_sample_at INTEGER,
    alerted INTEGER NOT NULL DEFAULT 0,
    last_value_x100 INTEGER
);

CREATE TABLE IF NOT EXISTS router_health_alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time INTEGER NOT NULL,
    resource TEXT NOT NULL CHECK (resource IN ('cpu','memory')),
    event_type TEXT NOT NULL CHECK (event_type IN ('warning','recovery')),
    value_x100 INTEGER NOT NULL,
    sent_at INTEGER
);

CREATE TABLE IF NOT EXISTS policy_rules (
    policy_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    policy_type TEXT NOT NULL CHECK (policy_type IN ('service_quota','schedule')),
    service TEXT NOT NULL DEFAULT 'internet',
    quota_bytes INTEGER,
    period TEXT CHECK (period IS NULL OR period IN ('daily')),
    days TEXT,
    start_minute INTEGER,
    end_minute INTEGER,
    action TEXT NOT NULL DEFAULT 'block' CHECK (action IN ('block')),
    enabled INTEGER NOT NULL DEFAULT 1,
    manual_override TEXT NOT NULL DEFAULT 'auto'
        CHECK (manual_override IN ('auto','allow','block')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_rule_devices (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_state (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    service_bytes INTEGER NOT NULL DEFAULT 0,
    blocked INTEGER NOT NULL DEFAULT 0,
    evaluated_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

-- One Telegram-ready record when a device first reaches a bandwidth quota on
-- a local day.  Schedule policies never create rows here.
CREATE TABLE IF NOT EXISTS policy_quota_alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    event_time INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    policy_name TEXT NOT NULL,
    quota_bytes INTEGER NOT NULL,
    used_bytes INTEGER NOT NULL,
    sent_at INTEGER,
    UNIQUE(policy_id, device_id, period_start)
);

CREATE INDEX IF NOT EXISTS idx_policy_quota_alerts_unsent
    ON policy_quota_alerts(sent_at, alert_id);

-- Evidence-backed custom-domain quota charges.  A charge is kept for the
-- local day after an exact client/domain/DNS-answer/destination match, so
-- expiry of the short live DNS window cannot erase it.
CREATE TABLE IF NOT EXISTS policy_usage_charges (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    bucket_start_utc INTEGER NOT NULL,
    bucket_seconds INTEGER NOT NULL,
    client_ip TEXT NOT NULL,
    destination_ip TEXT NOT NULL,
    domain TEXT NOT NULL,
    observed_at INTEGER NOT NULL,
    charged_bytes INTEGER NOT NULL,
    recorded_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start, bucket_start_utc,
                 client_ip, destination_ip),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_usage_charges_policy_day
    ON policy_usage_charges (policy_id, device_id, period_start, bucket_start_utc);

CREATE TABLE IF NOT EXISTS policy_usage_reset_points (
    policy_id INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    period_start TEXT NOT NULL,
    reset_at INTEGER NOT NULL,
    PRIMARY KEY (policy_id, device_id, period_start),
    FOREIGN KEY (policy_id) REFERENCES policy_rules(policy_id) ON DELETE CASCADE,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS policy_counter_baselines (
    service TEXT NOT NULL,
    ip TEXT NOT NULL,
    device_id INTEGER NOT NULL,
    bytes INTEGER NOT NULL DEFAULT 0,
    sampled_at INTEGER NOT NULL,
    PRIMARY KEY (service, ip),
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_policy_devices_device
    ON policy_rule_devices(device_id);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('schema_version','current');

COMMIT;
