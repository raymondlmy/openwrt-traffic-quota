-- Per-device manual overrides (r3s-policy override / Telegram commands).
-- 'allow' lifts every policy block for that one device; 'block' blocks the
-- device's whole internet access even when it has no policy. expires_at NULL
-- means until cleared. The evaluator deletes expired rows and logs them.
BEGIN;

CREATE TABLE IF NOT EXISTS device_overrides (
    device_id INTEGER PRIMARY KEY,
    action TEXT NOT NULL CHECK (action IN ('allow','block')),
    expires_at INTEGER,
    source TEXT NOT NULL CHECK (source IN ('cli','luci','telegram')),
    note TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id) ON DELETE CASCADE
);

-- Audit trail. notified_at is set once the Telegram bot has reported an
-- 'expire' event, so each expiry is announced exactly once.
CREATE TABLE IF NOT EXISTS device_override_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time INTEGER NOT NULL,
    device_id INTEGER NOT NULL,
    device_name TEXT NOT NULL,
    event TEXT NOT NULL CHECK (event IN ('allow','block','clear','expire')),
    expires_at INTEGER,
    source TEXT NOT NULL,
    note TEXT,
    notified_at INTEGER
);

CREATE INDEX IF NOT EXISTS idx_device_override_log_unnotified
    ON device_override_log(event, notified_at, log_id);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_013_device_overrides','applied');

COMMIT;
