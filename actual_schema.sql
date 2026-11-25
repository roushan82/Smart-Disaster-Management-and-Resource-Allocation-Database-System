
/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_UNIQUE_CHECKS=@@UNIQUE_CHECKS, UNIQUE_CHECKS=0 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;
DROP TABLE IF EXISTS `allocations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `allocations` (
  `allocation_id` int NOT NULL AUTO_INCREMENT,
  `request_id` int NOT NULL,
  `resource_id` int NOT NULL,
  `allocated_quantity` int NOT NULL,
  `allocation_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  `status` varchar(30) DEFAULT 'ALLOCATED',
  PRIMARY KEY (`allocation_id`),
  KEY `fk_allocations_request` (`request_id`),
  KEY `fk_allocations_resource` (`resource_id`),
  CONSTRAINT `fk_allocations_request` FOREIGN KEY (`request_id`) REFERENCES `emergency_requests` (`request_id`),
  CONSTRAINT `fk_allocations_resource` FOREIGN KEY (`resource_id`) REFERENCES `resources` (`resource_id`),
  CONSTRAINT `chk_allocations_quantity` CHECK ((`allocated_quantity` > 0)),
  CONSTRAINT `chk_allocations_status` CHECK ((`status` in (_cp850'ALLOCATED',_cp850'RELEASED',_cp850'CANCELLED')))
) ENGINE=InnoDB AUTO_INCREMENT=45 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `disasters`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `disasters` (
  `disaster_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `type` varchar(50) NOT NULL,
  `severity` varchar(20) NOT NULL,
  `description` text,
  `location_id` int NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'ACTIVE',
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`disaster_id`),
  KEY `fk_disasters_location` (`location_id`),
  CONSTRAINT `fk_disasters_location` FOREIGN KEY (`location_id`) REFERENCES `locations` (`location_id`),
  CONSTRAINT `chk_disasters_severity` CHECK ((`severity` in (_cp850'LOW',_cp850'MEDIUM',_cp850'HIGH',_cp850'CRITICAL'))),
  CONSTRAINT `chk_disasters_status` CHECK ((`status` in (_cp850'ACTIVE',_cp850'RESOLVED')))
) ENGINE=InnoDB AUTO_INCREMENT=23 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_disaster_type_before_insert` BEFORE INSERT ON `disasters` FOR EACH ROW BEGIN
    IF BINARY NEW.type NOT IN (
        'Flood', 'Earthquake', 'Fire', 'Cyclone', 'Landslide', 'Drought',
        'Tsunami', 'Industrial Accident'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid disaster type';
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_disaster_type_before_update` BEFORE UPDATE ON `disasters` FOR EACH ROW BEGIN
    IF NOT (BINARY NEW.type <=> BINARY OLD.type)
       AND BINARY NEW.type NOT IN (
           'Flood', 'Earthquake', 'Fire', 'Cyclone', 'Landslide', 'Drought',
           'Tsunami', 'Industrial Accident'
       ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid disaster type';
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
DROP TABLE IF EXISTS `emergency_requests`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `emergency_requests` (
  `request_id` int NOT NULL AUTO_INCREMENT,
  `disaster_id` int NOT NULL,
  `resource_type_id` int NOT NULL,
  `requested_quantity` int NOT NULL,
  `priority` varchar(20) NOT NULL,
  `status` varchar(30) NOT NULL DEFAULT 'PENDING',
  `request_time` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`request_id`),
  KEY `fk_requests_disaster` (`disaster_id`),
  KEY `fk_requests_resource_type` (`resource_type_id`),
  CONSTRAINT `fk_requests_disaster` FOREIGN KEY (`disaster_id`) REFERENCES `disasters` (`disaster_id`),
  CONSTRAINT `fk_requests_resource_type` FOREIGN KEY (`resource_type_id`) REFERENCES `resource_types` (`type_id`),
  CONSTRAINT `chk_requests_priority` CHECK ((`priority` in (_cp850'LOW',_cp850'MEDIUM',_cp850'HIGH',_cp850'CRITICAL'))),
  CONSTRAINT `chk_requests_quantity` CHECK ((`requested_quantity` > 0)),
  CONSTRAINT `chk_requests_status` CHECK ((`status` in (_cp850'PENDING',_cp850'ALLOCATED',_cp850'PARTIALLY_ALLOCATED',_cp850'REJECTED',_cp850'COMPLETED')))
) ENGINE=InnoDB AUTO_INCREMENT=173 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `locations`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `locations` (
  `location_id` int NOT NULL AUTO_INCREMENT,
  `city` varchar(100) NOT NULL,
  `state` varchar(100) NOT NULL,
  `address` varchar(255) DEFAULT NULL,
  `latitude` decimal(10,7) DEFAULT NULL,
  `longitude` decimal(10,7) DEFAULT NULL,
  PRIMARY KEY (`location_id`)
) ENGINE=InnoDB AUTO_INCREMENT=19 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `notifications`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `notifications` (
  `notification_id` bigint NOT NULL AUTO_INCREMENT,
  `event_key` varchar(191) NOT NULL,
  `recipient_user_id` int DEFAULT NULL,
  `event_type` varchar(48) NOT NULL,
  `message` varchar(500) NOT NULL,
  `related_request_id` int DEFAULT NULL,
  `related_disaster_id` int DEFAULT NULL,
  `related_team_id` int DEFAULT NULL,
  `related_personnel_id` int DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `read_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`notification_id`),
  UNIQUE KEY `uq_notifications_event_key` (`event_key`),
  KEY `idx_notifications_unread` (`recipient_user_id`,`read_at`,`created_at`),
  KEY `fk_notifications_request` (`related_request_id`),
  KEY `fk_notifications_disaster` (`related_disaster_id`),
  KEY `fk_notifications_team` (`related_team_id`),
  KEY `fk_notifications_personnel` (`related_personnel_id`),
  CONSTRAINT `fk_notifications_disaster` FOREIGN KEY (`related_disaster_id`) REFERENCES `disasters` (`disaster_id`),
  CONSTRAINT `fk_notifications_personnel` FOREIGN KEY (`related_personnel_id`) REFERENCES `personnel` (`personnel_id`),
  CONSTRAINT `fk_notifications_request` FOREIGN KEY (`related_request_id`) REFERENCES `emergency_requests` (`request_id`),
  CONSTRAINT `fk_notifications_team` FOREIGN KEY (`related_team_id`) REFERENCES `rescue_teams` (`team_id`),
  CONSTRAINT `fk_notifications_user` FOREIGN KEY (`recipient_user_id`) REFERENCES `users` (`user_id`)
) ENGINE=InnoDB AUTO_INCREMENT=80 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `personnel`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `personnel` (
  `personnel_id` int NOT NULL AUTO_INCREMENT,
  `team_id` int NOT NULL,
  `name` varchar(100) NOT NULL,
  `role` varchar(100) DEFAULT NULL,
  `contact` varchar(20) DEFAULT NULL,
  `status` varchar(20) DEFAULT 'AVAILABLE',
  PRIMARY KEY (`personnel_id`),
  KEY `fk_personnel_team` (`team_id`),
  CONSTRAINT `fk_personnel_team` FOREIGN KEY (`team_id`) REFERENCES `rescue_teams` (`team_id`),
  CONSTRAINT `chk_personnel_status` CHECK ((`status` in (_cp850'AVAILABLE',_cp850'DEPLOYED',_cp850'UNAVAILABLE')))
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_personnel_before_insert` BEFORE INSERT ON `personnel` FOR EACH ROW BEGIN
    IF NEW.role IS NULL OR BINARY NEW.role NOT IN (
        'Doctor', 'Nurse', 'Paramedic', 'Rescue Worker', 'Firefighter', 'Driver',
        'Team Leader'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid personnel role';
    END IF;
    IF NEW.contact IS NULL OR NEW.contact NOT REGEXP '^[6-9][0-9]{9}$' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid Indian mobile number';
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_personnel_before_update` BEFORE UPDATE ON `personnel` FOR EACH ROW BEGIN
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
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
DROP TABLE IF EXISTS `rescue_teams`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `rescue_teams` (
  `team_id` int NOT NULL AUTO_INCREMENT,
  `team_name` varchar(100) NOT NULL,
  `team_type` varchar(50) DEFAULT NULL,
  `location_id` int DEFAULT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'AVAILABLE',
  PRIMARY KEY (`team_id`),
  KEY `fk_rescue_teams_location` (`location_id`),
  CONSTRAINT `fk_rescue_teams_location` FOREIGN KEY (`location_id`) REFERENCES `locations` (`location_id`),
  CONSTRAINT `chk_rescue_teams_status` CHECK ((`status` in (_cp850'AVAILABLE',_cp850'DEPLOYED',_cp850'UNAVAILABLE')))
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_team_before_insert` BEFORE INSERT ON `rescue_teams` FOR EACH ROW BEGIN
    IF NEW.team_type IS NULL OR BINARY NEW.team_type NOT IN (
        'Flood Rescue', 'Fire & Rescue', 'Medical Response', 'Search & Rescue',
        'Earthquake Response', 'Evacuation Team', 'Hazardous Materials Response'
    ) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Invalid rescue team type';
    END IF;
    IF NEW.location_id IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Team location is required';
    END IF;
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
/*!50003 SET @saved_cs_client      = @@character_set_client */ ;
/*!50003 SET @saved_cs_results     = @@character_set_results */ ;
/*!50003 SET @saved_col_connection = @@collation_connection */ ;
/*!50003 SET character_set_client  = utf8mb4 */ ;
/*!50003 SET character_set_results = utf8mb4 */ ;
/*!50003 SET collation_connection  = utf8mb4_0900_ai_ci */ ;
/*!50003 SET @saved_sql_mode       = @@sql_mode */ ;
/*!50003 SET sql_mode              = 'ONLY_FULL_GROUP_BY,STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION' */ ;
DELIMITER ;;
/*!50003 CREATE*/ /*!50017 DEFINER=`root`@`localhost`*/ /*!50003 TRIGGER `validate_team_before_update` BEFORE UPDATE ON `rescue_teams` FOR EACH ROW BEGIN
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
END */;;
DELIMITER ;
/*!50003 SET sql_mode              = @saved_sql_mode */ ;
/*!50003 SET character_set_client  = @saved_cs_client */ ;
/*!50003 SET character_set_results = @saved_cs_results */ ;
/*!50003 SET collation_connection  = @saved_col_connection */ ;
DROP TABLE IF EXISTS `resource_types`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `resource_types` (
  `type_id` int NOT NULL AUTO_INCREMENT,
  `type_name` varchar(100) NOT NULL,
  `unit` varchar(30) NOT NULL,
  PRIMARY KEY (`type_id`),
  UNIQUE KEY `uq_resource_types_name` (`type_name`)
) ENGINE=InnoDB AUTO_INCREMENT=22 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `resources`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `resources` (
  `resource_id` int NOT NULL AUTO_INCREMENT,
  `type_id` int NOT NULL,
  `location_id` int NOT NULL,
  `total_quantity` int NOT NULL,
  `available_quantity` int NOT NULL,
  `status` varchar(20) NOT NULL DEFAULT 'AVAILABLE',
  PRIMARY KEY (`resource_id`),
  KEY `fk_resources_type` (`type_id`),
  KEY `fk_resources_location` (`location_id`),
  CONSTRAINT `fk_resources_location` FOREIGN KEY (`location_id`) REFERENCES `locations` (`location_id`),
  CONSTRAINT `fk_resources_type` FOREIGN KEY (`type_id`) REFERENCES `resource_types` (`type_id`),
  CONSTRAINT `chk_resources_available` CHECK ((`available_quantity` >= 0)),
  CONSTRAINT `chk_resources_quantity` CHECK ((`available_quantity` <= `total_quantity`)),
  CONSTRAINT `chk_resources_status` CHECK ((`status` in (_cp850'AVAILABLE',_cp850'UNAVAILABLE'))),
  CONSTRAINT `chk_resources_total` CHECK ((`total_quantity` >= 0))
) ENGINE=InnoDB AUTO_INCREMENT=23 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `scheduler_control`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `scheduler_control` (
  `control_id` tinyint NOT NULL,
  `generation` bigint NOT NULL DEFAULT '0',
  PRIMARY KEY (`control_id`),
  CONSTRAINT `chk_scheduler_control_singleton` CHECK ((`control_id` = 1))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `scheduling_run_items`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `scheduling_run_items` (
  `run_id` int NOT NULL,
  `queue_position` int NOT NULL,
  `request_id` int NOT NULL,
  `request_status` varchar(30) NOT NULL,
  `outcome` varchar(20) NOT NULL,
  `result_message` text NOT NULL,
  `executed_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`run_id`,`queue_position`),
  UNIQUE KEY `uq_scheduling_run_request` (`run_id`,`request_id`),
  KEY `idx_scheduling_request` (`request_id`),
  CONSTRAINT `fk_scheduling_item_request` FOREIGN KEY (`request_id`) REFERENCES `emergency_requests` (`request_id`),
  CONSTRAINT `fk_scheduling_item_run` FOREIGN KEY (`run_id`) REFERENCES `scheduling_runs` (`run_id`),
  CONSTRAINT `chk_scheduling_item_outcome` CHECK ((`outcome` in (_utf8mb4'PROCESSING',_utf8mb4'ALLOCATED',_utf8mb4'FAILED',_utf8mb4'SKIPPED'))),
  CONSTRAINT `chk_scheduling_queue_position` CHECK ((`queue_position` > 0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `scheduling_runs`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `scheduling_runs` (
  `run_id` int NOT NULL AUTO_INCREMENT,
  `algorithm` varchar(20) NOT NULL DEFAULT 'FCFS',
  `reason` varchar(255) NOT NULL DEFAULT 'Historical FCFS run',
  `automatic` tinyint(1) NOT NULL DEFAULT '0',
  `status` varchar(20) NOT NULL DEFAULT 'RUNNING',
  `started_at` timestamp NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `finished_at` timestamp NULL DEFAULT NULL,
  PRIMARY KEY (`run_id`),
  CONSTRAINT `chk_scheduling_runs_algorithm` CHECK ((`algorithm` in (_utf8mb4'FCFS',_utf8mb4'PRIORITY'))),
  CONSTRAINT `chk_scheduling_runs_automatic` CHECK ((`automatic` in (0,1))),
  CONSTRAINT `chk_scheduling_runs_status` CHECK ((`status` in (_utf8mb4'RUNNING',_utf8mb4'COMPLETED',_utf8mb4'FAILED')))
) ENGINE=InnoDB AUTO_INCREMENT=332 DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `shelters`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `shelters` (
  `shelter_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(150) NOT NULL,
  `location_id` int NOT NULL,
  `capacity` int NOT NULL,
  `occupied` int NOT NULL DEFAULT '0',
  `status` varchar(20) DEFAULT 'OPEN',
  PRIMARY KEY (`shelter_id`),
  KEY `fk_shelters_location` (`location_id`),
  CONSTRAINT `fk_shelters_location` FOREIGN KEY (`location_id`) REFERENCES `locations` (`location_id`),
  CONSTRAINT `chk_shelters_capacity` CHECK ((`capacity` > 0)),
  CONSTRAINT `chk_shelters_occupancy` CHECK ((`occupied` <= `capacity`)),
  CONSTRAINT `chk_shelters_occupied` CHECK ((`occupied` >= 0)),
  CONSTRAINT `chk_shelters_status` CHECK ((`status` in (_cp850'OPEN',_cp850'FULL',_cp850'CLOSED')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
DROP TABLE IF EXISTS `users`;
/*!40101 SET @saved_cs_client     = @@character_set_client */;
/*!50503 SET character_set_client = utf8mb4 */;
CREATE TABLE `users` (
  `user_id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(100) NOT NULL,
  `email` varchar(150) NOT NULL,
  `password_hash` varchar(255) NOT NULL,
  `role` varchar(30) NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`),
  UNIQUE KEY `uq_users_email` (`email`),
  CONSTRAINT `chk_users_role` CHECK ((`role` in (_cp850'ADMIN',_cp850'DISASTER_OFFICER',_cp850'RESOURCE_MANAGER',_cp850'RESCUER')))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
/*!40101 SET character_set_client = @saved_cs_client */;
/*!40103 SET TIME_ZONE=@OLD_TIME_ZONE */;

/*!40101 SET SQL_MODE=@OLD_SQL_MODE */;
/*!40014 SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS */;
/*!40014 SET UNIQUE_CHECKS=@OLD_UNIQUE_CHECKS */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;
/*!40111 SET SQL_NOTES=@OLD_SQL_NOTES */;

