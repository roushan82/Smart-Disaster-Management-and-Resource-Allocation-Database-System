-- Smart Disaster Management System - schema (MySQL 8.0.16+, InnoDB).
-- Mirrors the real `disaster_management` database (verified against a mysqldump
-- of it). Fresh install:  mysql -u <user> -p < database/schema.sql
-- (creates the database, tables, constraints, triggers; safe to re-run)

CREATE DATABASE IF NOT EXISTS disaster_management
    CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE disaster_management;

-- ---------------------------------------------------------------- reference data
CREATE TABLE IF NOT EXISTS users (
    user_id INT NOT NULL AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    email VARCHAR(150) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role VARCHAR(30) NOT NULL,
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_users_email (email),
    CONSTRAINT chk_users_role CHECK (role IN (
        'ADMIN', 'DISASTER_OFFICER', 'RESOURCE_MANAGER', 'RESCUER'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS locations (
    location_id INT NOT NULL AUTO_INCREMENT,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(100) NOT NULL,
    address VARCHAR(255) DEFAULT NULL,
    latitude DECIMAL(10,7) DEFAULT NULL,
    longitude DECIMAL(10,7) DEFAULT NULL,
    PRIMARY KEY (location_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS resource_types (
    type_id INT NOT NULL AUTO_INCREMENT,
    type_name VARCHAR(100) NOT NULL,
    unit VARCHAR(30) NOT NULL,
    PRIMARY KEY (type_id),
    UNIQUE KEY uq_resource_types_name (type_name)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 1. disasters
-- `type` is validated by the triggers below (not by a CHECK constraint).
CREATE TABLE IF NOT EXISTS disasters (
    disaster_id INT NOT NULL AUTO_INCREMENT,
    name VARCHAR(150) NOT NULL,
    type VARCHAR(50) NOT NULL,
    severity VARCHAR(20) NOT NULL,
    description TEXT,
    location_id INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (disaster_id),
    KEY fk_disasters_location (location_id),
    CONSTRAINT fk_disasters_location FOREIGN KEY (location_id) REFERENCES locations (location_id),
    CONSTRAINT chk_disasters_severity CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT chk_disasters_status CHECK (status IN ('ACTIVE', 'RESOLVED'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 2. resources
CREATE TABLE IF NOT EXISTS resources (
    resource_id INT NOT NULL AUTO_INCREMENT,
    type_id INT NOT NULL,
    location_id INT NOT NULL,
    total_quantity INT NOT NULL,
    available_quantity INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
    PRIMARY KEY (resource_id),
    KEY fk_resources_type (type_id),
    KEY fk_resources_location (location_id),
    CONSTRAINT fk_resources_type FOREIGN KEY (type_id) REFERENCES resource_types (type_id),
    CONSTRAINT fk_resources_location FOREIGN KEY (location_id) REFERENCES locations (location_id),
    CONSTRAINT chk_resources_available CHECK (available_quantity >= 0),
    CONSTRAINT chk_resources_quantity CHECK (available_quantity <= total_quantity),
    CONSTRAINT chk_resources_status CHECK (status IN ('AVAILABLE', 'UNAVAILABLE')),
    CONSTRAINT chk_resources_total CHECK (total_quantity >= 0)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 3. emergency requests
CREATE TABLE IF NOT EXISTS emergency_requests (
    request_id INT NOT NULL AUTO_INCREMENT,
    disaster_id INT NOT NULL,
    resource_type_id INT NOT NULL,
    requested_quantity INT NOT NULL,
    priority VARCHAR(20) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    request_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (request_id),
    KEY fk_requests_disaster (disaster_id),
    KEY fk_requests_resource_type (resource_type_id),
    CONSTRAINT fk_requests_disaster FOREIGN KEY (disaster_id) REFERENCES disasters (disaster_id),
    CONSTRAINT fk_requests_resource_type FOREIGN KEY (resource_type_id) REFERENCES resource_types (type_id),
    CONSTRAINT chk_requests_priority CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    CONSTRAINT chk_requests_quantity CHECK (requested_quantity > 0),
    CONSTRAINT chk_requests_status CHECK (status IN (
        'PENDING', 'ALLOCATED', 'PARTIALLY_ALLOCATED', 'REJECTED', 'COMPLETED'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS allocations (
    allocation_id INT NOT NULL AUTO_INCREMENT,
    request_id INT NOT NULL,
    resource_id INT NOT NULL,
    allocated_quantity INT NOT NULL,
    allocation_time TIMESTAMP NULL DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(30) DEFAULT 'ALLOCATED',
    PRIMARY KEY (allocation_id),
    KEY fk_allocations_request (request_id),
    KEY fk_allocations_resource (resource_id),
    CONSTRAINT fk_allocations_request FOREIGN KEY (request_id) REFERENCES emergency_requests (request_id),
    CONSTRAINT fk_allocations_resource FOREIGN KEY (resource_id) REFERENCES resources (resource_id),
    CONSTRAINT chk_allocations_quantity CHECK (allocated_quantity > 0),
    CONSTRAINT chk_allocations_status CHECK (status IN ('ALLOCATED', 'RELEASED', 'CANCELLED'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 4. rescue teams
-- `team_type` / `location_id` are validated by the triggers below.
CREATE TABLE IF NOT EXISTS rescue_teams (
    team_id INT NOT NULL AUTO_INCREMENT,
    team_name VARCHAR(100) NOT NULL,
    team_type VARCHAR(50) DEFAULT NULL,
    location_id INT DEFAULT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'AVAILABLE',
    PRIMARY KEY (team_id),
    KEY fk_rescue_teams_location (location_id),
    CONSTRAINT fk_rescue_teams_location FOREIGN KEY (location_id) REFERENCES locations (location_id),
    CONSTRAINT chk_rescue_teams_status CHECK (status IN ('AVAILABLE', 'DEPLOYED', 'UNAVAILABLE'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 5. personnel
-- `role` / `contact` are validated by the triggers below.
CREATE TABLE IF NOT EXISTS personnel (
    personnel_id INT NOT NULL AUTO_INCREMENT,
    team_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    role VARCHAR(100) DEFAULT NULL,
    contact VARCHAR(20) DEFAULT NULL,
    status VARCHAR(20) DEFAULT 'AVAILABLE',
    PRIMARY KEY (personnel_id),
    KEY fk_personnel_team (team_id),
    CONSTRAINT fk_personnel_team FOREIGN KEY (team_id) REFERENCES rescue_teams (team_id),
    CONSTRAINT chk_personnel_status CHECK (status IN ('AVAILABLE', 'DEPLOYED', 'UNAVAILABLE'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 6. team assignments
CREATE TABLE IF NOT EXISTS team_request_assignments (
    assignment_id INT NOT NULL AUTO_INCREMENT,
    request_id INT NOT NULL,
    team_id INT NOT NULL,
    assigned_by_user_id INT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ASSIGNED',
    instructions TEXT NULL,
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (assignment_id),
    UNIQUE KEY uq_assignment_request (request_id),
    CONSTRAINT fk_assignment_request FOREIGN KEY (request_id) REFERENCES emergency_requests (request_id),
    CONSTRAINT fk_assignment_team FOREIGN KEY (team_id) REFERENCES rescue_teams (team_id),
    CONSTRAINT fk_assignment_user FOREIGN KEY (assigned_by_user_id) REFERENCES users (user_id),
    CONSTRAINT chk_assignment_status CHECK (status IN (
        'ASSIGNED', 'ACCEPTED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 7. notifications
CREATE TABLE IF NOT EXISTS notifications (
    notification_id BIGINT NOT NULL AUTO_INCREMENT,
    event_key VARCHAR(191) NOT NULL,
    recipient_user_id INT NULL,
    event_type VARCHAR(48) NOT NULL,
    message VARCHAR(500) NOT NULL,
    related_request_id INT NULL,
    related_disaster_id INT NULL,
    related_team_id INT NULL,
    related_personnel_id INT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (notification_id),
    UNIQUE KEY uq_notifications_event_key (event_key),
    KEY idx_notifications_unread (recipient_user_id, read_at, created_at),
    KEY fk_notifications_request (related_request_id),
    KEY fk_notifications_disaster (related_disaster_id),
    KEY fk_notifications_team (related_team_id),
    KEY fk_notifications_personnel (related_personnel_id),
    CONSTRAINT fk_notifications_user FOREIGN KEY (recipient_user_id) REFERENCES users (user_id),
    CONSTRAINT fk_notifications_request FOREIGN KEY (related_request_id) REFERENCES emergency_requests (request_id),
    CONSTRAINT fk_notifications_disaster FOREIGN KEY (related_disaster_id) REFERENCES disasters (disaster_id),
    CONSTRAINT fk_notifications_team FOREIGN KEY (related_team_id) REFERENCES rescue_teams (team_id),
    CONSTRAINT fk_notifications_personnel FOREIGN KEY (related_personnel_id) REFERENCES personnel (personnel_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 8. scheduling log + worker signal
CREATE TABLE IF NOT EXISTS scheduling_runs (
    run_id INT NOT NULL AUTO_INCREMENT,
    algorithm VARCHAR(20) NOT NULL DEFAULT 'FCFS',
    reason VARCHAR(255) NOT NULL DEFAULT 'Historical FCFS run',
    automatic TINYINT(1) NOT NULL DEFAULT 0,
    rr_start_request_id INT DEFAULT NULL COMMENT 'RR rotation anchor; next RR run starts after this request ID',
    status VARCHAR(20) NOT NULL DEFAULT 'RUNNING',
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (run_id),
    CONSTRAINT chk_scheduling_runs_algorithm CHECK (algorithm IN ('FCFS', 'PRIORITY', 'RR')),
    CONSTRAINT chk_scheduling_runs_automatic CHECK (automatic IN (0, 1)),
    CONSTRAINT chk_scheduling_runs_status CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scheduling_run_items (
    run_id INT NOT NULL,
    queue_position INT NOT NULL,
    request_id INT NOT NULL,
    request_status VARCHAR(30) NOT NULL,
    outcome VARCHAR(20) NOT NULL,
    result_message TEXT NOT NULL,
    executed_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (run_id, queue_position),
    UNIQUE KEY uq_scheduling_run_request (run_id, request_id),
    KEY idx_scheduling_request (request_id),
    CONSTRAINT fk_scheduling_item_run FOREIGN KEY (run_id) REFERENCES scheduling_runs (run_id),
    CONSTRAINT fk_scheduling_item_request FOREIGN KEY (request_id) REFERENCES emergency_requests (request_id),
    CONSTRAINT chk_scheduling_item_outcome CHECK (outcome IN ('PROCESSING', 'ALLOCATED', 'FAILED', 'SKIPPED')),
    CONSTRAINT chk_scheduling_queue_position CHECK (queue_position > 0)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS scheduler_control (
    control_id TINYINT NOT NULL,
    generation BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (control_id),
    CONSTRAINT chk_scheduler_control_singleton CHECK (control_id = 1)
) ENGINE=InnoDB;

INSERT IGNORE INTO scheduler_control (control_id, generation) VALUES (1, 0);

-- -------------------------------------------------------- historical data foundation
-- Historical observations are intentionally separate from live disasters,
-- emergency_requests, allocations, resources, and the automatic scheduler.
CREATE TABLE IF NOT EXISTS dataset_sources (
    source_id BIGINT NOT NULL AUTO_INCREMENT,
    source_key CHAR(64) NOT NULL,
    provider VARCHAR(120) NOT NULL,
    source_url VARCHAR(2048) NOT NULL,
    dataset_name VARCHAR(255) NOT NULL,
    dataset_version VARCHAR(100) DEFAULT NULL,
    license_attribution TEXT NOT NULL,
    retrieved_at DATETIME NOT NULL COMMENT 'Naive UTC value; application inputs are normalized to UTC',
    checksum VARCHAR(128) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'UTC instant; MySQL converts using the session time zone',
    PRIMARY KEY (source_id),
    UNIQUE KEY uq_dataset_sources_source_key (source_key)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS source_observations (
    observation_id BIGINT NOT NULL AUTO_INCREMENT,
    source_id BIGINT NOT NULL,
    source_record_id VARCHAR(191) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_as_cs NOT NULL,
    event_start_date DATE DEFAULT NULL,
    event_end_date DATE DEFAULT NULL,
    observed_at DATETIME DEFAULT NULL COMMENT 'Naive UTC value; application inputs are normalized to UTC',
    raw_location VARCHAR(500) DEFAULT NULL,
    normalized_location VARCHAR(255) DEFAULT NULL,
    raw_values JSON NOT NULL,
    raw_payload JSON NOT NULL,
    quality_status VARCHAR(24) NOT NULL DEFAULT 'UNASSESSED',
    imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'UTC instant; MySQL converts using the session time zone',
    PRIMARY KEY (observation_id),
    UNIQUE KEY uq_source_observations_source_record (source_id, source_record_id),
    KEY idx_source_observations_event_dates (event_start_date, event_end_date),
    CONSTRAINT fk_source_observations_source FOREIGN KEY (source_id)
        REFERENCES dataset_sources (source_id),
    CONSTRAINT chk_source_observations_quality CHECK (quality_status IN (
        'UNASSESSED', 'INCOMPLETE', 'AMBIGUOUS', 'INVALID', 'VERIFIED'
    ))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS detection_results (
    detection_id BIGINT NOT NULL AUTO_INCREMENT,
    observation_id BIGINT NOT NULL,
    rule_id VARCHAR(100) NOT NULL,
    rule_version VARCHAR(50) NOT NULL,
    evaluated_values JSON NOT NULL,
    explanation TEXT NOT NULL,
    candidate_priority VARCHAR(20) DEFAULT NULL,
    review_status VARCHAR(24) NOT NULL DEFAULT 'PENDING_REVIEW',
    evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'UTC instant; MySQL converts using the session time zone',
    reviewed_at TIMESTAMP DEFAULT NULL COMMENT 'UTC instant; MySQL converts using the session time zone',
    reviewer VARCHAR(150) DEFAULT NULL,
    PRIMARY KEY (detection_id),
    UNIQUE KEY uq_detection_results_rule (observation_id, rule_id, rule_version),
    KEY idx_detection_results_review_status (review_status, evaluated_at),
    CONSTRAINT fk_detection_results_observation FOREIGN KEY (observation_id)
        REFERENCES source_observations (observation_id),
    CONSTRAINT chk_detection_results_priority CHECK (
        candidate_priority IS NULL OR candidate_priority IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')
    ),
    CONSTRAINT chk_detection_results_review_status CHECK (review_status IN (
        'PENDING_REVIEW', 'NEEDS_MORE_DATA', 'APPROVED', 'REJECTED'
    ))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS detection_audit (
    audit_id BIGINT NOT NULL AUTO_INCREMENT,
    source_id BIGINT DEFAULT NULL,
    observation_id BIGINT DEFAULT NULL,
    detection_id BIGINT DEFAULT NULL,
    action VARCHAR(40) NOT NULL,
    actor VARCHAR(150) NOT NULL,
    details JSON DEFAULT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT 'UTC instant; MySQL converts using the session time zone',
    PRIMARY KEY (audit_id),
    KEY idx_detection_audit_source (source_id, created_at),
    KEY idx_detection_audit_observation (observation_id, created_at),
    KEY idx_detection_audit_detection (detection_id, created_at),
    CONSTRAINT fk_detection_audit_source FOREIGN KEY (source_id)
        REFERENCES dataset_sources (source_id),
    CONSTRAINT fk_detection_audit_observation FOREIGN KEY (observation_id)
        REFERENCES source_observations (observation_id),
    CONSTRAINT fk_detection_audit_detection FOREIGN KEY (detection_id)
        REFERENCES detection_results (detection_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- 9. shelters
-- Exists in the real database; not used by the application code yet.
CREATE TABLE IF NOT EXISTS shelters (
    shelter_id INT NOT NULL AUTO_INCREMENT,
    name VARCHAR(150) NOT NULL,
    location_id INT NOT NULL,
    capacity INT NOT NULL,
    occupied INT NOT NULL DEFAULT 0,
    status VARCHAR(20) DEFAULT 'OPEN',
    PRIMARY KEY (shelter_id),
    KEY fk_shelters_location (location_id),
    CONSTRAINT fk_shelters_location FOREIGN KEY (location_id) REFERENCES locations (location_id),
    CONSTRAINT chk_shelters_capacity CHECK (capacity > 0),
    CONSTRAINT chk_shelters_occupancy CHECK (occupied <= capacity),
    CONSTRAINT chk_shelters_occupied CHECK (occupied >= 0),
    CONSTRAINT chk_shelters_status CHECK (status IN ('OPEN', 'FULL', 'CLOSED'))
) ENGINE=InnoDB;

-- ---------------------------------------------------------------- triggers (validation)
-- DROP + CREATE so re-running this file never fails on an existing trigger.
DELIMITER ;;

DROP TRIGGER IF EXISTS validate_disaster_type_before_insert;;
CREATE TRIGGER validate_disaster_type_before_insert BEFORE INSERT ON disasters
FOR EACH ROW
BEGIN
    IF BINARY NEW.type NOT IN (
        'Flood', 'Earthquake', 'Fire', 'Cyclone', 'Landslide', 'Drought',
        'Tsunami', 'Industrial Accident'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid disaster type';
    END IF;
END;;

DROP TRIGGER IF EXISTS validate_disaster_type_before_update;;
CREATE TRIGGER validate_disaster_type_before_update BEFORE UPDATE ON disasters
FOR EACH ROW
BEGIN
    IF NOT (BINARY NEW.type <=> BINARY OLD.type)
       AND BINARY NEW.type NOT IN (
           'Flood', 'Earthquake', 'Fire', 'Cyclone', 'Landslide', 'Drought',
           'Tsunami', 'Industrial Accident'
       ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid disaster type';
    END IF;
END;;

DROP TRIGGER IF EXISTS validate_team_before_insert;;
CREATE TRIGGER validate_team_before_insert BEFORE INSERT ON rescue_teams
FOR EACH ROW
BEGIN
    IF NEW.team_type IS NULL OR BINARY NEW.team_type NOT IN (
        'Flood Rescue', 'Fire & Rescue', 'Medical Response', 'Search & Rescue',
        'Earthquake Response', 'Evacuation Team', 'Hazardous Materials Response'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid rescue team type';
    END IF;
    IF NEW.location_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Team location is required';
    END IF;
END;;

DROP TRIGGER IF EXISTS validate_team_before_update;;
CREATE TRIGGER validate_team_before_update BEFORE UPDATE ON rescue_teams
FOR EACH ROW
BEGIN
    IF NOT (BINARY NEW.team_type <=> BINARY OLD.team_type)
       AND (NEW.team_type IS NULL OR BINARY NEW.team_type NOT IN (
           'Flood Rescue', 'Fire & Rescue', 'Medical Response', 'Search & Rescue',
           'Earthquake Response', 'Evacuation Team', 'Hazardous Materials Response'
       )) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid rescue team type';
    END IF;
    IF NOT (NEW.location_id <=> OLD.location_id) AND NEW.location_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Team location is required';
    END IF;
END;;

DROP TRIGGER IF EXISTS validate_personnel_before_insert;;
CREATE TRIGGER validate_personnel_before_insert BEFORE INSERT ON personnel
FOR EACH ROW
BEGIN
    IF NEW.role IS NULL OR BINARY NEW.role NOT IN (
        'Doctor', 'Nurse', 'Paramedic', 'Rescue Worker', 'Firefighter', 'Driver',
        'Team Leader'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid personnel role';
    END IF;
    IF NEW.contact IS NULL OR NEW.contact NOT REGEXP '^[6-9][0-9]{9}$' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid Indian mobile number';
    END IF;
END;;

DROP TRIGGER IF EXISTS validate_personnel_before_update;;
CREATE TRIGGER validate_personnel_before_update BEFORE UPDATE ON personnel
FOR EACH ROW
BEGIN
    IF NOT (BINARY NEW.role <=> BINARY OLD.role)
       AND (NEW.role IS NULL OR BINARY NEW.role NOT IN (
           'Doctor', 'Nurse', 'Paramedic', 'Rescue Worker', 'Firefighter',
           'Driver', 'Team Leader'
       )) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid personnel role';
    END IF;
    IF NOT (NEW.contact <=> OLD.contact)
       AND (NEW.contact IS NULL OR NEW.contact NOT REGEXP '^[6-9][0-9]{9}$') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid Indian mobile number';
    END IF;
END;;

-- These guards reject ordinary UPDATE/DELETE. The application DB account
-- should also have only SELECT/INSERT on this table (no UPDATE, DELETE,
-- ALTER, DROP, or TRUNCATE privileges). A privileged operator can bypass
-- these controls; this is not a tamper-proof audit log.
DROP TRIGGER IF EXISTS prevent_detection_audit_update;;
CREATE TRIGGER prevent_detection_audit_update BEFORE UPDATE ON detection_audit
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'detection_audit is append-only';
END;;

DROP TRIGGER IF EXISTS prevent_detection_audit_delete;;
CREATE TRIGGER prevent_detection_audit_delete BEFORE DELETE ON detection_audit
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'detection_audit is append-only';
END;;

DELIMITER ;