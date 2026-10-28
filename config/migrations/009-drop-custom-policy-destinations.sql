BEGIN;

-- Custom-domain blocks use only live resolver and recent AdGuard answers.
-- Remove the short-lived inventory introduced by migration 008.
DROP TABLE IF EXISTS policy_custom_destinations;

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_009_drop_custom_policy_destinations','applied');

COMMIT;
