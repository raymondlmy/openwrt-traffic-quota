-- Game DNS block: Telegram alerts when the evaluator cannot update AdGuard
-- Home (wrong password, AdGuard down) and when it recovers. Failure state
-- (consecutive failures, alerted flag) lives in schema_meta.
BEGIN;

CREATE TABLE IF NOT EXISTS game_dns_alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_time INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('failing','recovered')),
    detail TEXT NOT NULL,
    sent_at INTEGER
);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_015_game_dns_alerts','applied');

COMMIT;
