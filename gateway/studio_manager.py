import threading
import logging
import sqlite3

logger = logging.getLogger(__name__)

ANTPLUS_NETWORK_KEY = [0xB9, 0xA5, 0x21, 0xFB, 0xBD, 0x72, 0xC3, 0x45]


class StudioManager:
    """
    Manages one ANT+ USB dongle and opens HR channels for paired straps.
    Straps must have ant_device_id set in the DB.
    Runs openant in a daemon thread (synchronous library).
    """

    def __init__(self, cache_db: str, broadcast_fn, event_loop, session_mgr=None):
        self.cache_db    = cache_db
        self.broadcast   = broadcast_fn
        self.loop        = event_loop
        self.session_mgr = session_mgr
        self._node       = None
        self._channels   = {}   # ant_device_id → StrapChannel
        self._lock       = threading.Lock()

    def _load_rows(self) -> list:
        db = sqlite3.connect(self.cache_db)
        rows = db.execute("""
            SELECT s.ant_device_id,
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
            WHERE  s.ant_device_id IS NOT NULL
        """).fetchall()
        db.close()
        return rows

    def load_and_start(self):
        rows = self._load_rows()
        if not rows:
            logger.info("Žiadne ANT+ pásy nakonfigurované — preskakujem")
            return

        try:
            from ant.easy.node import Node
            node = Node()
            node.set_network_key(0x00, ANTPLUS_NETWORK_KEY)
            self._node = node
        except Exception as e:
            logger.error(f"ANT+ dongle sa nedá inicializovať: {e}")
            return

        from strap_channel import StrapChannel
        for ch_num, (dev_id, position, bike_label, name, max_hr, weight_kg, birth_year, gender) in enumerate(rows):
            ch = StrapChannel(
                node          = node,
                channel_num   = ch_num,
                ant_device_id = dev_id,
                bike_position = position,
                bike_label    = bike_label,
                rider_name    = name,
                max_hr        = max_hr,
                weight_kg     = weight_kg,
                birth_year    = birth_year,
                gender        = gender,
                broadcast_fn  = self.broadcast,
                event_loop    = self.loop,
                session_mgr   = self.session_mgr,
            )
            with self._lock:
                self._channels[dev_id] = ch

        t = threading.Thread(target=self._node.start, daemon=True, name="ant-dongle")
        t.start()
        logger.info(f"ANT+ manager štart — {len(rows)} kanálov")

    def reload(self):
        """Zastaví dongle a znovu načíta konfig."""
        if self._node:
            try:
                self._node.stop()
            except Exception:
                pass
            self._node = None
        with self._lock:
            self._channels.clear()
        self.load_and_start()

    def get_status(self) -> list:
        with self._lock:
            return [
                {
                    "position":      ch.bike_position,
                    "bike_label":    ch.bike_label,
                    "name":          ch.rider_name,
                    "connected":     ch.connected,
                    "hr":            ch.last_hr,
                    "battery":       None,
                    "ant_device_id": ch.ant_device_id,
                }
                for ch in self._channels.values()
            ]
