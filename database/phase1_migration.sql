USE disaster_management;

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

DELIMITER ;;

CREATE TRIGGER prevent_detection_audit_update BEFORE UPDATE ON detection_audit
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'detection_audit is append-only';
END;;

CREATE TRIGGER prevent_detection_audit_delete BEFORE DELETE ON detection_audit
FOR EACH ROW
BEGIN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'detection_audit is append-only';
END;;

DELIMITER ;
