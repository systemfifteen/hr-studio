import asyncio
import json
import logging
import os
import time

import websockets
from bleak import BleakScanner
from ble_manager import BleManager

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
SCAN_TIMEOUT = 10.0   # sekúnd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
)
logger = logging.getLogger("gateway")

CACHE_DB         = os.getenv("LOCAL_CACHE_DB", "/data/local_cache.db")
WS_PORT          = int(os.getenv("WS_PORT", 8765))
RELOAD_PORT      = int(os.getenv("GATEWAY_RELOAD_PORT", 8766))
WATCHDOG_TIMEOUT = 5    # sekúnd bez signálu = pás odpojený

connected_clients: set = set()
manager: BleManager | None = None


async def broadcast(data: dict):
    if not connected_clients:
        return
    msg = json.dumps(data)
    await asyncio.gather(
        *[c.send(msg) for c in connected_clients],
        return_exceptions=True,
    )


async def ws_handler(ws, path):
    connected_clients.add(ws)
    logger.info(f"Klient pripojený: {ws.remote_address} (celkom: {len(connected_clients)})")

    if manager:
        await ws.send(json.dumps({
            "type":   "initial_state",
            "riders": manager.get_status(),
        }))

    try:
        await ws.wait_closed()
    finally:
        connected_clients.discard(ws)
        logger.info(f"Klient odpojený (zostatok: {len(connected_clients)})")


async def watchdog():
    """Každé 2s skontroluje timestamps — detekuje tiché výpadky pásov."""
    while True:
        if manager:
            now = time.time()
            for strap in manager.straps.values():
                if strap.connected and (now - strap.last_seen) > WATCHDOG_TIMEOUT:
                    strap.connected = False
                    await broadcast({
                        "type":     "signal_lost",
                        "position": strap.bike_position,
                        "name":     strap.rider_name,
                    })
                    logger.warning(f"Watchdog: stratený signál — {strap.rider_name}")
        await asyncio.sleep(2)


async def reload_http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """Minimálny HTTP server — POST /reload a GET /scan."""
    try:
        first_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode().strip()
        # drain zvyšok requestu
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        parts  = first_line.split()
        method = parts[0] if parts else ""
        path   = parts[1].split("?")[0] if len(parts) > 1 else "/"

        if method == "POST" and path == "/reload":
            if manager:
                logger.info("HTTP reload — reloadujem BLE pásy")
                await manager.reload()
                await broadcast({"type": "riders_updated"})
                body = b'{"ok": true}'
            else:
                body = b'{"ok": false, "error": "manager not ready"}'

        elif method == "GET" and path == "/scan":
            logger.info(f"BLE scan — hľadám HR zariadenia ({SCAN_TIMEOUT}s)...")
            devices = await BleakScanner.discover(
                timeout=SCAN_TIMEOUT,
                service_uuids=[HEART_RATE_SERVICE_UUID],
            )
            result = [
                {"name": d.name or "Unknown", "address": d.address, "rssi": d.rssi}
                for d in sorted(devices, key=lambda x: -(x.rssi or -999))
            ]
            logger.info(f"BLE scan hotový — {len(result)} HR zariadení")
            body = json.dumps(result).encode()

        else:
            body = b'{"error": "not found"}'

        response = (
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            b"Access-Control-Allow-Origin: *\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error(f"HTTP handler chyba: {e}")
    finally:
        writer.close()


async def main():
    global manager

    manager = BleManager(
        cache_db    = CACHE_DB,
        broadcast_fn= broadcast,
    )
    await manager.load_and_start()

    reload_server = await asyncio.start_server(
        reload_http_handler, "0.0.0.0", RELOAD_PORT
    )
    logger.info(f"Reload HTTP endpoint na porte {RELOAD_PORT}")

    logger.info(f"WebSocket server štartuje na porte {WS_PORT}")
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        async with reload_server:
            await asyncio.gather(
                watchdog(),
                asyncio.Future(),
            )


if __name__ == "__main__":
    asyncio.run(main())
