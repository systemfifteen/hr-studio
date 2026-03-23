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

-- BLE pásy priradené k bicyklom
INSERT OR IGNORE INTO straps(id, ble_address, bike_id, label) VALUES
    (2, 'D3:B3:0B:C7:0A:88',  7, ''),
    (3, 'E2:17:78:4D:F5:48', 13, ''),
    (4, 'EE:9C:B9:BD:20:AD',  1, ''),
    (5, 'D1:6E:D6:44:13:15', 15, 'D1:6E:D6:44:13:15');

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

-- Jazdci priradení k bicyklom
INSERT OR IGNORE INTO riders_cache(bike_position, name, max_hr, weight_kg, birth_year, gender) VALUES
    ( 1, 'rusinka', 170, 85.0, 1972, 'M'),
    ( 7, 'PP',      171, 89.0, 1973, 'M'),
    (13, 'Sona',    170, 60.0, 1985, 'F'),
    (15, '15',      174, 115.0, 1978, 'M');

CREATE TABLE IF NOT EXISTS strap_catalog (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    ble_name    TEXT,
    ble_address TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS riders_catalog (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    birth_year      INTEGER NOT NULL,
    weight_kg       REAL,
    gender          TEXT NOT NULL DEFAULT 'M',
    max_hr_override INTEGER
);

-- Stáli jazdci
INSERT OR IGNORE INTO riders_catalog(id, name, birth_year, weight_kg, gender) VALUES
    (1, 'rusinka', 1972, 85.0, 'M'),
    (2, 'PP',      1973, 89.0, 'M'),
    (3, 'Sona',    1985, 60.0, 'F'),
    (4, '15',      1978, 115.0, 'M');

COMMIT;
