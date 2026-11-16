-- Per-device 'allow' overrides that also lift site policies (/game).
-- A plain allow (/extend) lifts whole-internet schedules only; with
-- lift_sites=1 it also lifts that device's custom-site quotas and site
-- schedules (game quotas) until it expires. Only set with action 'allow'.
-- ALTER TABLE ADD COLUMN is not idempotent: postinst runs this file only
-- when device_overrides has no lift_sites column yet.
BEGIN;

ALTER TABLE device_overrides
    ADD COLUMN lift_sites INTEGER NOT NULL DEFAULT 0 CHECK (lift_sites IN (0,1));
ALTER TABLE device_override_log
    ADD COLUMN lift_sites INTEGER NOT NULL DEFAULT 0;

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_014_override_lift_sites','applied');

COMMIT;
