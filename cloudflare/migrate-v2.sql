-- Apply this once only to a D1 database created with the pre-v2 schema.
ALTER TABLE sensor_events ADD COLUMN validation_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE sensor_events ADD COLUMN validation_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sensor_events ADD COLUMN validation_completed_at TEXT;
ALTER TABLE sensor_events ADD COLUMN validation_result_json TEXT;

UPDATE sensor_events
SET validation_status = CASE
    WHEN requires_validation = 0 THEN 'not_required'
    WHEN notified_at IS NOT NULL THEN 'confirmed'
    ELSE 'pending'
END;

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
