-- Queue one Telegram alert per policy/device/local day when a bandwidth
-- quota is first reached.  Schedule restrictions intentionally have no rows.
BEGIN;

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

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_010_policy_quota_alerts','applied');

COMMIT;
