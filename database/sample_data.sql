-- Optional demo data:  mysql -u <user> -p disaster_management < database/sample_data.sql
INSERT INTO locations (city, state) VALUES
    ('Mumbai', 'Maharashtra'), ('Chennai', 'Tamil Nadu'),
    ('Guwahati', 'Assam'), ('Dehradun', 'Uttarakhand');

INSERT INTO resource_types (type_name, unit) VALUES
    ('Food Packets', 'packets'), ('Medicine Kits', 'kits'),
    ('Ambulances', 'vehicles'), ('Oxygen Cylinders', 'cylinders');

INSERT INTO resources (type_id, location_id, total_quantity, available_quantity, status) VALUES
    (1, 1, 1000, 1000, 'AVAILABLE'), (2, 1, 200, 200, 'AVAILABLE'),
    (3, 2, 10, 10, 'AVAILABLE'),     (4, 3, 50, 50, 'AVAILABLE');

INSERT INTO disasters (name, type, severity, description, location_id) VALUES
    ('Assam Floods 2026', 'Flood', 'CRITICAL', 'Brahmaputra overflow', 3),
    ('Uttarakhand Landslide', 'Landslide', 'HIGH', 'Road blocked near Dehradun', 4);

INSERT INTO rescue_teams (team_name, team_type, location_id) VALUES
    ('Alpha Flood Unit', 'Flood Rescue', 3), ('Bravo Medical', 'Medical Response', 4);

INSERT INTO personnel (team_id, name, role, contact) VALUES
    (1, 'Ravi Kumar', 'Team Leader', '9876543210'),
    (2, 'Dr. Anita Rao', 'Doctor', '9123456780');