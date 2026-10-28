-- Store lightweight hourly CPU, memory and load summaries for Telegram.
BEGIN;

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

INSERT OR REPLACE INTO schema_meta (key,value)
VALUES ('migration_005_router_health','applied');

COMMIT;
