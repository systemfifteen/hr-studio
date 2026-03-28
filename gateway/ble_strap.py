import asyncio
import logging
import time
from datetime import datetime

from bleak import BleakClient, BleakError

from hr_utils import calc_zone, calc_calories, calc_watts

logger = logging.getLogger(__name__)

HEART_RATE_UUID  = "00002a37-0000-1000-8000-00805f9b34fb"
BATTERY_UUID     = "00002a19-0000-1000-8000-00805f9b34fb"
RECONNECT_DELAY  = 5    # sekúnd medzi pokusmi o reconnect
CONNECT_TIMEOUT  = 30.0  # MZ-1 potrebuje ~20s inicializácie pred prvým platným HR


class BleStrap:
    """
    Jeden BLE hrudný pás = jeden asyncio Task.
    Automaticky sa reconnectuje pri výpadku.
    """

    def __init__(
        self,
        ble_address: str,
        bike_position: int,
        bike_label: str,
        rider_name: str,
        max_hr: int,
        weight_kg: float | None,
        birth_year: int | None,
        gender: str | None,
        broadcast_fn,
        session_mgr=None,
        start_delay: float = 0.0,
        connect_lock: asyncio.Lock | None = None,
        scan_event: asyncio.Event | None = None,
    ):
        self.ble_address   = ble_address
        self.bike_position = bike_position
        self.bike_label    = bike_label
        self.rider_name    = rider_name
        self.max_hr        = max_hr
        self.weight_kg     = weight_kg
        self.birth_year    = birth_year
        self.gender        = gender
        self.broadcast_fn  = broadcast_fn
        self.session_mgr   = session_mgr
        self.start_delay   = start_delay
        self.connect_lock  = connect_lock
        self.scan_event    = scan_event

        self.connected      = False
        self.last_seen      = 0.0
        self.last_hr        = 0
        self.battery        = None   # % alebo None ak nepodporované
        self.total_calories = 0.0
        self.total_meps     = 0.0
        self._running       = True
        self._task: asyncio.Task | None = None

    def start(self):
        self._task = asyncio.create_task(
            self._connect_loop(),
            name=f"strap-{self.bike_position}",
        )

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ─── Hlavná slučka ────────────────────────────────────────────────────────

    async def _connect_loop(self):
        if self.start_delay > 0:
            await asyncio.sleep(self.start_delay)
        while self._running:
            client = BleakClient(self.ble_address, timeout=CONNECT_TIMEOUT)
            try:
                logger.info(
                    f"Pripájam {self.ble_address} → "
                    f"pozícia {self.bike_position} ({self.rider_name})"
                )
                # Serializuj pokusy o pripojenie — BlueZ zvláda len jedno naraz.
                # Ak prebieha scan, uvoľni cestu: pokúšaj sa získať lock po 1s
                # úsekoch a pred každým pokusom skontroluj scan_event.
                lock = self.connect_lock if self.connect_lock else asyncio.Lock()
                lock_acquired = False
                while self._running and not lock_acquired:
                    if self.scan_event and self.scan_event.is_set():
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        await asyncio.wait_for(lock.acquire(), timeout=1.0)
                        lock_acquired = True
                    except asyncio.TimeoutError:
                        pass  # skús znova — re-check scan_event
                if not self._running:
                    break
                try:
                    await client.connect()
                finally:
                    lock.release()

                # Lock uvoľnený — BlueZ môže spracovať ďalší pás
                await client.start_notify(HEART_RATE_UUID, self._on_hr_data)
                try:
                    bat = await client.read_gatt_char(BATTERY_UUID)
                    self.battery = round(bat[0] / 10) * 10  # zaokrúhli na 10%
                except Exception:
                    self.battery = None
                logger.info(
                    f"BLE spojené: {self.rider_name} ({self.ble_address})"
                    + (f" — batéria {self.battery}%" if self.battery is not None else "")
                )

                while client.is_connected and self._running:
                    await asyncio.sleep(1)

            except (BleakError, asyncio.TimeoutError) as e:
                logger.warning(f"BLE chyba [{self.ble_address}]: {e}")
            except asyncio.CancelledError:
                break   # finally zariadi disconnect
            except Exception as e:
                logger.warning(f"Neočakávaná BLE chyba [{self.ble_address}]: {e}")
            finally:
                if client.is_connected:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass

            if self.connected:
                self.connected = False
                asyncio.create_task(self.broadcast_fn({
                    "type":     "disconnected",
                    "position": self.bike_position,
                    "name":     self.rider_name,
                }))
                logger.warning(f"Pás odpojený — {self.rider_name}")

            if self._running:
                await asyncio.sleep(RECONNECT_DELAY)

    # ─── GATT HR notification callback ────────────────────────────────────────

    def _on_hr_data(self, _sender, data: bytearray):
        # GATT 0x2A37 Heart Rate Measurement
        # Byte 0: flags, bit0 = HR format (0=uint8, 1=uint16)
        flags = data[0]
        hr    = int.from_bytes(data[1:3], "little") if (flags & 0x01) else data[1]

        now       = time.time()
        prev_seen = self.last_seen   # uložíme pred update pre výpočty
        self.connected = True
        self.last_seen = now

        # HR=0 = inicializácia / žiadny kontakt — ignoruj broadcast, ale spojenie je OK
        if hr == 0:
            return

        zone        = calc_zone(hr, self.max_hr)
        pct         = min(100, round(hr / self.max_hr * 100))
        elapsed_min = (now - prev_seen) / 60 if prev_seen > 0 else 0.0

        cal_inc  = 0.0
        meps_inc = 0.0
        watts    = 0

        if self.weight_kg and self.birth_year and elapsed_min > 0:
            age     = datetime.now().year - self.birth_year
            cal_inc = calc_calories(
                hr, self.weight_kg,
                age=age,
                gender=self.gender or "M",
                duration_min=elapsed_min,
            )
            watts = calc_watts(hr, self.weight_kg, age=age, gender=self.gender or "M")
            self.total_calories += cal_inc

        # MEPs: Myzone Effort Points — zóna 4 aj 5 = 4 body/min
        if elapsed_min > 0:
            meps_inc = min(zone, 4) * elapsed_min
            self.total_meps += meps_inc

        self.last_hr = hr

        if self.session_mgr:
            self.session_mgr.record(
                self.bike_position, hr, zone, cal_inc, meps_inc, watts
            )

        asyncio.create_task(self.broadcast_fn({
            "type":       "hr_update",
            "position":   self.bike_position,
            "bike_label": self.bike_label,
            "name":       self.rider_name,
            "hr":       hr,
            "pct":      pct,
            "zone":     zone,
            "calories": round(self.total_calories),
            "meps":     round(self.total_meps),
            "watts":    watts,
            "max_hr":   self.max_hr,
            "battery":  self.battery,
        }))
