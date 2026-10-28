-- Track sustained CPU/memory thresholds and Telegram alert delivery.
BEGIN;

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

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_006_router_health_alerts','applied');

COMMIT;
