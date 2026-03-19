PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS bikes (
    id INTEGER PRIMARY KEY,
    position INTEGER UNIQUE NOT NULL,
    label TEXT NOT NULL,
    dongle_port TEXT,
    ant_channel INTEGER
);

-- 18 bicyklov podľa fyzického layoutu spinning sály
-- position = bike.number z FitReserve, label = fyzický štítok na bicykli
INSERT OR IGNORE INTO bikes(id, position, label) VALUES
    ( 1,  1, '2'),
    ( 2,  2, '14'),
    ( 3,  3, '1'),
    ( 4,  4, '3'),
    ( 5,  5, 'X1'),
    ( 6,  6, '69'),
    ( 7,  7, '110'),
    ( 8,  8, '4'),
    ( 9,  9, '5'),
    (10, 10, '6'),
    (11, 11, '112'),
    (12, 12, '7'),
    (13, 13, '8'),
    (14, 14, '9'),
    (15, 15, '10'),
    (16, 16, '11'),
    (17, 17, '12'),
    (18, 18, '13');

CREATE TABLE IF NOT EXISTS straps (
    id INTEGER PRIMARY KEY,
    ble_address TEXT,
    bike_id INTEGER REFERENCES bikes(id),
    label TEXT
);

-- MZ-1 testovací pás priradený k bicyklu #1 (label "2")
INSERT OR IGNORE INTO straps(id, ble_address, bike_id, label)
    VALUES(1, 'D1:6E:D6:44:13:15', 1, 'MZ-1');

CREATE TABLE IF NOT EXISTS riders_cache (
    bike_position INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    max_hr INTEGER NOT NULL,
    weight_kg REAL,
    birth_year INTEGER,
    gender TEXT,
    reservation_id INTEGER,
    synced_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO riders_cache(bike_position, name, max_hr, weight_kg, birth_year, gender)
    VALUES(1, 'Peter', 185, 80.0, 1985, 'M');

COMMIT;
