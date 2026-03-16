import sqlite3
import threading
import logging
from collections import defaultdict
from strap_channel import StrapChannel

logger = logging.getLogger(__name__)

ANTPLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]


class StudioManager:
    """
    Načíta konfig z lokálnej SQLite cache a otvorí ANT+ kanály.
    Každý dongle beží v samostatnom daemone vlákne.
    """

    def __init__(self, cache_db: str, broadcast_fn, event_loop):
        self.cache_db    = cache_db
        self.broadcast   = broadcast_fn
        self.loop        = event_loop
        self.nodes       = {}     # port → Node
        self.channels    = {}     # ant_device_id → StrapChannel
        self._lock       = threading.Lock()

    def load_and_start(self):
        db   = sqlite3.connect(self.cache_db)
        rows = db.execute("""
            SELECT s.ant_device_id,
                   b.position,
                   b.dongle_port,
                   b.ant_channel,
                   r.name,
                   r.max_hr,
                   r.weight_kg,
                   r.birth_year,
                   r.gender
            FROM   straps s
            JOIN   bikes b         ON b.id = s.bike_id
            JOIN   riders_cache r  ON r.bike_position = b.position
        """).fetchall()
        db.close()

        if not rows:
            logger.warning("Žiadni riders v cache — spúšťam bez kanálov")
            return

        by_dongle = defaultdict(list)
        for row in rows:
            by_dongle[row[2]].append(row)

        for port, straps in by_dongle.items():
            self._start_dongle(port, straps)

        logger.info(f"StudioManager štart — {len(rows)} kanálov na {len(by_dongle)} dongle")

    def _start_dongle(self, port: str, straps: list):
        try:
            from ant.easy.node import Node
            from ant.base.driver import SerialDriver
            node = Node(driver=SerialDriver(port))
            node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
            self.nodes[port] = node

            for (dev_id, position, _, ch_num, name, max_hr, weight_kg, birth_year, gender) in straps:
                ch = StrapChannel(
                    node          = node,
                    channel_num   = ch_num,
                    ant_device_id = dev_id,
                    bike_position = position,
                    rider_name    = name,
                    max_hr        = max_hr,
                    weight_kg     = weight_kg,
                    birth_year    = birth_year,
                    gender        = gender,
                    broadcast_fn  = self.broadcast,
                    event_loop    = self.loop,
                )
                with self._lock:
                    self.channels[dev_id] = ch

            t = threading.Thread(target=node.start, daemon=True, name=f"dongle-{port}")
            t.start()
            logger.info(f"Dongle {port} štartovaný ({len(straps)} kanálov)")

        except Exception as e:
            logger.error(f"Dongle {port} failed: {e}")

    def reload(self):
        """Zavolaj po sync — zatvorí staré kanály a otvorí nové."""
        logger.info("Reload kanálov z aktualizovanej cache...")
        for port, node in self.nodes.items():
            try:
                node.stop()
            except Exception:
                pass
        self.nodes.clear()
        self.channels.clear()
        self.load_and_start()

    def get_status(self) -> list:
        with self._lock:
            return [
                {
                    "position":  ch.bike_position,
                    "name":      ch.rider_name,
                    "connected": ch.connected,
                    "hr":        ch.last_hr,
                    "device_id": ch.ant_device_id,
                }
                for ch in self.channels.values()
            ]
