import time
import asyncio
import logging
from hr_utils import calc_zone

logger = logging.getLogger(__name__)


class StrapChannel:
    """
    Jeden ANT+ kanál = jeden hrudný pás.
    Otvára sa s pevným device_id — nescannuje okolie.
    """

    def __init__(
        self,
        node,
        channel_num: int,
        ant_device_id: int,
        bike_position: int,
        rider_name: str,
        max_hr: int,
        weight_kg: float,
        birth_year: int,
        gender: str,
        broadcast_fn,
        event_loop,
    ):
        self.ant_device_id = ant_device_id
        self.bike_position = bike_position
        self.rider_name    = rider_name
        self.max_hr        = max_hr
        self.weight_kg     = weight_kg
        self.birth_year    = birth_year
        self.gender        = gender
        self.broadcast_fn  = broadcast_fn
        self.loop          = event_loop

        self.connected  = False
        self.last_seen  = 0.0
        self.last_hr    = 0
        self.session_start = time.time()
        self.total_calories = 0.0

        try:
            from ant.easy.channel import Channel
            ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
            ch.set_id(ant_device_id, 0x78, 0)   # 0x78 = ANT+ HR profil
            ch.set_period(8070)                   # ~4 Hz
            ch.set_rf_freq(57)                    # 2457 MHz
            ch.set_search_timeout(10)             # 25s potom timeout
            ch.on_broadcast_data = self._on_data
            ch.on_search_timeout = self._on_timeout
            ch.open()
            logger.info(
                f"Kanál {channel_num} otvorený — "
                f"device {ant_device_id} → pozícia {bike_position} ({rider_name})"
            )
        except Exception as e:
            logger.error(f"Chyba pri otváraní kanála {channel_num}: {e}")

    def _on_data(self, data: bytes):
        hr   = data[7]
        now  = time.time()
        zone = calc_zone(hr, self.max_hr)
        pct  = round(hr / self.max_hr * 100)

        # Kalórie — inkrementálne (1s intervaly)
        if self.connected and self.weight_kg and self.birth_year:
            from hr_utils import calc_calories
            elapsed_min = (now - self.last_seen) / 60
            from datetime import datetime
            age = datetime.now().year - self.birth_year
            self.total_calories += calc_calories(
                hr, self.weight_kg,
                age=age,
                gender=self.gender or "M",
                duration_min=elapsed_min,
            )

        self.connected = True
        self.last_seen = now
        self.last_hr   = hr

        self._emit({
            "type":      "hr_update",
            "position":  self.bike_position,
            "name":      self.rider_name,
            "hr":        hr,
            "pct":       pct,
            "zone":      zone,
            "calories":  round(self.total_calories),
            "max_hr":    self.max_hr,
        })

    def _on_timeout(self):
        if self.connected:
            logger.warning(
                f"Pás stratený — pozícia {self.bike_position} ({self.rider_name})"
            )
        self.connected = False
        self._emit({
            "type":     "disconnected",
            "position": self.bike_position,
            "name":     self.rider_name,
        })

    def _emit(self, data: dict):
        asyncio.run_coroutine_threadsafe(
            self.broadcast_fn(data), self.loop
        )
