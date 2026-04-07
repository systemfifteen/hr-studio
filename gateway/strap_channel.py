import time
import asyncio
import logging
from datetime import datetime

from hr_utils import calc_zone, calc_calories, calc_watts

logger = logging.getLogger(__name__)


class StrapChannel:
    """
    Jeden ANT+ kanál = jeden hrudný pás.
    Otvára sa s pevným device_id — nescannuje okolie.
    Callbacks sú volané z ANT+ vlákna → broadcast cez asyncio.run_coroutine_threadsafe.
    """

    def __init__(
        self,
        node,
        channel_num: int,
        ant_device_id: int,
        bike_position: int,
        bike_label: str,
        rider_name: str,
        max_hr: int,
        weight_kg: float | None,
        birth_year: int | None,
        gender: str | None,
        broadcast_fn,
        event_loop,
        session_mgr=None,
    ):
        self.ant_device_id = ant_device_id
        self.bike_position = bike_position
        self.bike_label    = bike_label
        self.rider_name    = rider_name
        self.max_hr        = max_hr
        self.weight_kg     = weight_kg
        self.birth_year    = birth_year
        self.gender        = gender
        self.broadcast_fn  = broadcast_fn
        self.loop          = event_loop
        self.session_mgr   = session_mgr

        self.connected      = False
        self.last_seen      = 0.0
        self.last_hr        = 0
        self.total_calories = 0.0
        self.total_meps     = 0.0

        try:
            from openant.easy.channel import Channel
            ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
            ch.set_id(ant_device_id, 0x78, 0)   # 0x78 = ANT+ HR profil
            ch.set_period(8070)                   # ~4 Hz
            ch.set_rf_freq(57)                    # 2457 MHz
            ch.set_search_timeout(10)             # ~25s potom timeout
            ch.on_broadcast_data = self._on_data
            ch.on_search_timeout = self._on_timeout
            ch.open()
            logger.info(
                f"ANT+ kanál {channel_num}: device {ant_device_id} "
                f"→ pozícia {bike_position} ({rider_name})"
            )
        except Exception as e:
            logger.error(f"Chyba pri otváraní ANT+ kanála {channel_num}: {e}")

    def _on_data(self, data: bytes):
        hr  = data[7]
        now = time.time()

        prev_seen      = self.last_seen
        self.connected = True
        self.last_seen = now
        self.last_hr   = hr

        if hr == 0:
            return

        zone = calc_zone(hr, self.max_hr)
        pct  = round(hr / self.max_hr * 100)

        elapsed_min = (now - prev_seen) / 60 if prev_seen > 0 else 0.0
        cal_inc  = 0.0
        meps_inc = 0.0
        watts    = 0

        if self.weight_kg and self.birth_year and elapsed_min > 0:
            age     = datetime.now().year - self.birth_year
            cal_inc = calc_calories(
                hr, self.weight_kg,
                age=age, gender=self.gender or "M",
                duration_min=elapsed_min,
            )
            watts = calc_watts(hr, self.weight_kg, age=age, gender=self.gender or "M")
            self.total_calories += cal_inc

        if elapsed_min > 0:
            meps_inc = min(zone, 4) * elapsed_min
            self.total_meps += meps_inc

        if self.session_mgr:
            self.session_mgr.record(self.bike_position, hr, zone, cal_inc, meps_inc, watts)

        self._emit({
            "type":       "hr_update",
            "position":   self.bike_position,
            "bike_label": self.bike_label,
            "name":       self.rider_name,
            "hr":         hr,
            "pct":        pct,
            "zone":       zone,
            "calories":   round(self.total_calories),
            "meps":       round(self.total_meps),
            "watts":      watts,
            "max_hr":     self.max_hr,
            "battery":    None,
        })

    def _on_timeout(self):
        if self.connected:
            logger.warning(
                f"ANT+ pás stratený — pozícia {self.bike_position} ({self.rider_name})"
            )
        self.connected = False
        self._emit({
            "type":     "disconnected",
            "position": self.bike_position,
            "name":     self.rider_name,
        })

    def _emit(self, data: dict):
        asyncio.run_coroutine_threadsafe(self.broadcast_fn(data), self.loop)
