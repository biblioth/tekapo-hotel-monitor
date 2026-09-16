CREATE TABLE IF NOT EXISTS sensor_cycles (
    id TEXT PRIMARY KEY,
    scheduled_at TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    checked_count INTEGER NOT NULL DEFAULT 0,
    available_count INTEGER NOT NULL DEFAULT 0,
    unknown_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    event_count INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER
);

CREATE TABLE IF NOT EXISTS sensor_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT NOT NULL,
    hotel_key TEXT NOT NULL,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL,
    offers_json TEXT NOT NULL,
    message TEXT,
    duration_ms INTEGER NOT NULL,
    observed_at TEXT NOT NULL,
    FOREIGN KEY(cycle_id) REFERENCES sensor_cycles(id)
);

CREATE INDEX IF NOT EXISTS idx_sensor_observations_cycle
    ON sensor_observations(cycle_id);
CREATE INDEX IF NOT EXISTS idx_sensor_observations_hotel_time
    ON sensor_observations(hotel_key, observed_at DESC);

CREATE TABLE IF NOT EXISTS sensor_snapshots (
    hotel_key TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    offers_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    consecutive_unknown INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sensor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    cycle_id TEXT NOT NULL,
    hotel_key TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    requires_validation INTEGER NOT NULL DEFAULT 1,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_attempts INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    validation_dispatched_at TEXT,
    validation_completed_at TEXT,
    validation_result_json TEXT,
    notified_at TEXT,
    notify_attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS idx_sensor_events_pending
    ON sensor_events(notified_at, id);
CREATE INDEX IF NOT EXISTS idx_sensor_events_validation
    ON sensor_events(validation_status, validation_dispatched_at, id);

CREATE TABLE IF NOT EXISTS sensor_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id INTEGER NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    queue_enqueued_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(event_id, channel),
    FOREIGN KEY(event_id) REFERENCES sensor_events(id)
);

CREATE INDEX IF NOT EXISTS idx_sensor_deliveries_pending
    ON sensor_deliveries(status, queue_enqueued_at, id);

CREATE TABLE IF NOT EXISTS sensor_summary_deliveries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    summary_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    queue_enqueued_at TEXT,
    delivered_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(summary_date, channel)
);

CREATE INDEX IF NOT EXISTS idx_sensor_summary_deliveries_pending
    ON sensor_summary_deliveries(status, queue_enqueued_at, id);
