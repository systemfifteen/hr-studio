PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS bikes (
    id INTEGER PRIMARY KEY,
    position INTEGER UNIQUE NOT NULL,
    label TEXT NOT NULL,
    dongle_port TEXT,
    ant_channel INTEGER
);

-- 18 bicyklov podľa fyzického layoutu spinning sály + X2 (testovací)
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
    (18, 18, '13'),
    (19, 19, 'X2');

CREATE TABLE IF NOT EXISTS straps (
    id INTEGER PRIMARY KEY,
    ble_address TEXT,
    ant_device_id INTEGER,
    bike_id INTEGER REFERENCES bikes(id),
    label TEXT
);


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
    (1, 'rusinka', 1972, 85.0, 'M');

COMMIT;
