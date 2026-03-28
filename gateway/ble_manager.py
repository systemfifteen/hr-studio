import asyncio
import logging
import sqlite3

from ble_strap import BleStrap

logger = logging.getLogger(__name__)


class BleManager:
    """
    Načíta konfig z lokálnej SQLite cache a spustí BLE spojenia.
    Každý pás beží ako samostatný asyncio Task.
    """

    def __init__(self, cache_db: str, broadcast_fn, session_mgr=None):
        self.cache_db    = cache_db
        self.broadcast   = broadcast_fn
        self.session_mgr = session_mgr
        self.straps: dict[str, BleStrap] = {}   # ble_address → BleStrap
        self._connect_lock = asyncio.Lock()      # BlueZ: len jedno pripojenie naraz
        self._scan_event   = asyncio.Event()     # keď je set → straps nečakajú na lock

    def _load_rows(self) -> list:
        db = sqlite3.connect(self.cache_db)
        rows = db.execute("""
            SELECT s.ble_address,
                   b.position,
                   b.label,
                   r.name,
                   r.max_hr,
                   r.weight_kg,
                   r.birth_year,
                   r.gender
            FROM   straps s
            JOIN   bikes b        ON b.id = s.bike_id
            JOIN   riders_cache r ON r.bike_position = b.position
            WHERE  s.ble_address IS NOT NULL
        """).fetchall()
        db.close()
        return rows

    async def load_and_start(self):
        rows = self._load_rows()
        if not rows:
            logger.warning("Žiadni riders v cache alebo žiadne BLE adresy — čakám na admin setup")
            return

        for idx, (ble_addr, position, bike_label, name, max_hr, weight_kg, birth_year, gender) in enumerate(rows):
            strap = BleStrap(
                ble_address   = ble_addr,
                bike_position = position,
                bike_label    = bike_label,
                rider_name    = name,
                max_hr        = max_hr,
                weight_kg     = weight_kg,
                birth_year    = birth_year,
                gender        = gender,
                broadcast_fn  = self.broadcast,
                session_mgr   = self.session_mgr,
                start_delay   = idx * 2.0,
                connect_lock  = self._connect_lock,
                scan_event    = self._scan_event,
            )
            self.straps[ble_addr] = strap
            strap.start()

        logger.info(f"BleManager štart — {len(rows)} pásov")

    async def reload(self):
        """Smart reload — zachová bežiace pásy, zastaví len zrušené, spustí nové."""
        logger.info("BLE smart reload z aktualizovanej cache...")
        new_rows = self._load_rows()
        new_conf = {row[0]: row for row in new_rows}  # ble_address → row

        # Zastav pásy ktoré už nie sú v konfigurácii
        to_stop = [addr for addr in list(self.straps) if addr not in new_conf]
        for addr in to_stop:
            await self.straps[addr].stop()
            del self.straps[addr]

        # Aktualizuj bežiace pásy / spusti nové
        new_straps = []
        for ble_addr, row in new_conf.items():
            _, position, bike_label, name, max_hr, weight_kg, birth_year, gender = row
            if ble_addr in self.straps:
                # Strap beží — len aktualizuj metadata, zachovaj total_calories/total_meps
                s = self.straps[ble_addr]
                s.bike_position = position
                s.bike_label    = bike_label
                s.rider_name    = name
                s.max_hr        = max_hr
                s.weight_kg     = weight_kg
                s.birth_year    = birth_year
                s.gender        = gender
            else:
                new_straps.append(row)

        for idx, (ble_addr, position, bike_label, name, max_hr, weight_kg, birth_year, gender) in enumerate(new_straps):
            strap = BleStrap(
                ble_address   = ble_addr,
                bike_position = position,
                bike_label    = bike_label,
                rider_name    = name,
                max_hr        = max_hr,
                weight_kg     = weight_kg,
                birth_year    = birth_year,
                gender        = gender,
                broadcast_fn  = self.broadcast,
                session_mgr   = self.session_mgr,
                start_delay   = idx * 2.0,
                connect_lock  = self._connect_lock,
                scan_event    = self._scan_event,
            )
            self.straps[ble_addr] = strap
            strap.start()

        logger.info(
            f"BLE reload hotový — {len(self.straps)} aktívnych, "
            f"{len(new_straps)} nových, {len(to_stop)} zastavených"
        )

    def get_status(self) -> list:
        return [
            {
                "position":    s.bike_position,
                "bike_label":  s.bike_label,
                "name":        s.rider_name,
                "connected":   s.connected,
                "hr":          s.last_hr,
                "battery":     s.battery,
                "ble_address": s.ble_address,
            }
            for s in self.straps.values()
        ]
