USE disaster_management;

-- One-time migration for existing installations.
-- Adds the RR rotation anchor to scheduler run history and permits RR runs.
ALTER TABLE scheduling_runs
    DROP CHECK chk_scheduling_runs_algorithm,
    ADD COLUMN rr_start_request_id INT DEFAULT NULL
        COMMENT 'RR rotation anchor; next RR run starts after this request ID',
    ADD CONSTRAINT chk_scheduling_runs_algorithm
        CHECK (algorithm IN ('FCFS', 'PRIORITY', 'RR'));
