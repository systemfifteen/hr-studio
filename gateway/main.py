import asyncio
import json
import logging
import os
import time

import websockets
from ble_manager import BleManager
from admin_api import AdminApi   # sekúnd

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
admin_api: AdminApi | None = None


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


async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    """HTTP server pre admin API (port RELOAD_PORT)."""
    try:
        first_line = (await asyncio.wait_for(reader.readline(), timeout=5)).decode().strip()
        parts  = first_line.split()
        method = parts[0] if parts else "GET"
        path   = parts[1].split("?")[0] if len(parts) > 1 else "/"

        headers = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, val = line.decode().partition(":")
            headers[key.lower().strip()] = val.strip()

        body = b""
        content_length = int(headers.get("content-length", 0))
        if content_length > 0:
            body = await reader.read(content_length)

        # strip /api prefix
        api_path = path[4:] if path.startswith("/api") else path
        if not api_path:
            api_path = "/"

        status, result = await admin_api.handle(method, api_path, body)

        resp_body = json.dumps(result).encode()
        status_text = {200: "OK", 201: "Created", 204: "No Content",
                       400: "Bad Request", 404: "Not Found", 409: "Conflict"}.get(status, "OK")
        response = (
            f"HTTP/1.1 {status} {status_text}\r\n"
            f"Content-Type: application/json\r\n"
            f"Access-Control-Allow-Origin: *\r\n"
            f"Access-Control-Allow-Methods: GET, POST, PUT, DELETE, OPTIONS\r\n"
            f"Access-Control-Allow-Headers: Content-Type\r\n"
            f"Content-Length: {len(resp_body)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode() + resp_body
        writer.write(response)
        await writer.drain()
    except Exception as e:
        logger.error(f"HTTP handler chyba: {e}")
    finally:
        writer.close()


async def main():
    global manager, admin_api

    manager = BleManager(
        cache_db    = CACHE_DB,
        broadcast_fn= broadcast,
    )
    await manager.load_and_start()

    admin_api = AdminApi(
        cache_db    = CACHE_DB,
        manager     = manager,
        broadcast_fn= broadcast,
    )

    reload_server = await asyncio.start_server(
        http_handler, "0.0.0.0", RELOAD_PORT
    )
    logger.info(f"Admin API na porte {RELOAD_PORT}")

    logger.info(f"WebSocket server štartuje na porte {WS_PORT}")
    async with websockets.serve(ws_handler, "0.0.0.0", WS_PORT):
        async with reload_server:
            await asyncio.gather(
                watchdog(),
                asyncio.Future(),
            )


if __name__ == "__main__":
    asyncio.run(main())
