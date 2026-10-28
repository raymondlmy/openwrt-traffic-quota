BEGIN;

-- Retain policy-specific custom-domain destination observations long enough
-- to terminate an already-open CDN connection after a quota is reached.
CREATE TABLE IF NOT EXISTS policy_custom_destinations (
    policy_id INTEGER NOT NULL,
    destination_ip TEXT NOT NULL,
    last_seen INTEGER NOT NULL,
    PRIMARY KEY (policy_id, destination_ip)
);

CREATE INDEX IF NOT EXISTS idx_policy_custom_destinations_seen
    ON policy_custom_destinations (last_seen);

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_008_custom_policy_destinations','applied');

COMMIT;
