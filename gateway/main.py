import asyncio
import json
import logging
import os
import time

import websockets
from ble_manager import BleManager
from studio_manager import StudioManager
from admin_api import AdminApi
from session_manager import SessionManager   # sekúnd

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
ant_manager: StudioManager | None = None
admin_api: AdminApi | None = None
session_mgr: SessionManager | None = None


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

    all_riders = []
    if manager:
        all_riders.extend(manager.get_status())
    if ant_manager:
        all_riders.extend(ant_manager.get_status())
    if all_riders:
        await ws.send(json.dumps({
            "type":   "initial_state",
            "riders": all_riders,
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

        status_text = {200: "OK", 201: "Created", 204: "No Content",
                       400: "Bad Request", 404: "Not Found", 409: "Conflict"}.get(status, "OK")

        # FIT export — binárna odpoveď
        if isinstance(result, dict) and "__fit__" in result:
            fit_data = result["__fit__"]
            filename = result["filename"]
            response = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: application/octet-stream\r\n"
                f"Content-Disposition: attachment; filename=\"{filename}\"\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(fit_data)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + fit_data
        # CSV export — špeciálna odpoveď
        elif isinstance(result, dict) and "__csv__" in result:
            csv_data   = result["__csv__"].encode()
            filename   = f"session_{result['session_id']}.csv"
            response = (
                f"HTTP/1.1 200 OK\r\n"
                f"Content-Type: text/csv\r\n"
                f"Content-Disposition: attachment; filename=\"{filename}\"\r\n"
                f"Access-Control-Allow-Origin: *\r\n"
                f"Content-Length: {len(csv_data)}\r\n"
                f"Connection: close\r\n\r\n"
            ).encode() + csv_data
        else:
            resp_body = json.dumps(result).encode()
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


BIKE_LAYOUT = [
    (1,"2"),(2,"14"),(3,"1"),(4,"3"),(5,"X1"),(6,"69"),(7,"110"),
    (8,"4"),(9,"5"),(10,"6"),(11,"112"),(12,"7"),(13,"8"),(14,"9"),
    (15,"10"),(16,"11"),(17,"12"),(18,"13"),
]

def ensure_bikes():
    """Doplní chýbajúce bicykle do DB (bezpečné pri existujúcej DB)."""
    import sqlite3
    db = sqlite3.connect(CACHE_DB)
    db.executemany(
        "INSERT OR IGNORE INTO bikes(position, label) VALUES(?,?)",
        BIKE_LAYOUT,
    )
    db.commit()
    db.close()
    logger.info("Bikes: OK (18 bicyklov)")


async def main():
    global manager, ant_manager, admin_api, session_mgr

    ensure_bikes()
    session_mgr = SessionManager(cache_db=CACHE_DB, broadcast_fn=broadcast)

    manager = BleManager(
        cache_db    = CACHE_DB,
        broadcast_fn= broadcast,
        session_mgr = session_mgr,
    )
    await manager.load_and_start()

    ant_manager = StudioManager(
        cache_db    = CACHE_DB,
        broadcast_fn= broadcast,
        event_loop  = asyncio.get_running_loop(),
        session_mgr = session_mgr,
    )
    ant_manager.load_and_start()

    admin_api = AdminApi(
        cache_db    = CACHE_DB,
        manager     = manager,
        broadcast_fn= broadcast,
        session_mgr = session_mgr,
        ant_manager = ant_manager,
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
                session_mgr.flush_loop(),
                asyncio.Future(),
            )


if __name__ == "__main__":
    asyncio.run(main())
