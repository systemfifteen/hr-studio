PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE bikes (
    id INTEGER PRIMARY KEY,
    position INTEGER UNIQUE NOT NULL,
    label TEXT NOT NULL,
    dongle_port TEXT,
    ant_channel INTEGER
);
INSERT INTO bikes VALUES(1,1,'Bicykel 1',NULL,NULL);
CREATE TABLE straps (
    id INTEGER PRIMARY KEY,
    ble_address TEXT,
    bike_id INTEGER REFERENCES bikes(id),
    label TEXT
);
INSERT INTO straps VALUES(1,'D1:6E:D6:44:13:15',1,'MZ-1 test');
CREATE TABLE riders_cache (
    bike_position INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    max_hr INTEGER NOT NULL,
    weight_kg REAL,
    birth_year INTEGER,
    gender TEXT,
    reservation_id INTEGER,
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
INSERT INTO riders_cache VALUES(1,'Peter',185,80.0,1985,'M',NULL,'2026-03-18 14:15:59');
COMMIT;
