-- Store optional user-maintained domains for custom service policies.
BEGIN;
ALTER TABLE policy_rules ADD COLUMN domains TEXT;
INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_004_custom_domain_policies','applied');
COMMIT;
