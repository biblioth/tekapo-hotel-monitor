-- Add covering indexes for the five-minute sensor cycle and daily reporting.
-- Without these indexes, the growing cycle table is scanned repeatedly and can
-- exhaust the Workers Free D1 row-read allowance.
CREATE INDEX IF NOT EXISTS idx_sensor_cycles_status_started
    ON sensor_cycles(status, started_at);

CREATE INDEX IF NOT EXISTS idx_sensor_cycles_scheduled
    ON sensor_cycles(scheduled_at DESC);
