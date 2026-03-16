# Ako zistiť ANT+ device ID hrudného pásu

## Scan tool (spusti raz pri nastavovaní)

```bash
docker compose exec gateway python tools/scan_devices.py
```

Výstup:
```
Scanning ANT+ devices (30s)...
Found: device_id=12345  type=0x78 (HR)  signal=-65dBm
Found: device_id=67890  type=0x78 (HR)  signal=-72dBm
```

## scan_devices.py

```python
from ant.easy.node import Node
from ant.easy.channel import Channel
import time

ANTPLUS_KEY = [0xB9,0xA5,0x21,0xFB,0xBD,0x72,0xC3,0x45]
found = {}

def on_data(data):
    device_id = int.from_bytes(data[0:2], 'little')
    if device_id not in found:
        found[device_id] = True
        print(f"Found: device_id={device_id}  type=0x78 (HR)")

node = Node()
node.set_network_key(0x00, ANTPLUS_KEY)

ch = node.new_channel(Channel.Type.BIDIRECTIONAL_RECEIVE)
ch.set_id(0, 0x78, 0)     # device_id=0 = wildcard scan
ch.set_period(8070)
ch.set_rf_freq(57)
ch.set_search_timeout(0)  # bez timeoutu
ch.on_broadcast_data = on_data
ch.open()

print("Scanning ANT+ devices (30s)...")
print("Nasaď pásy na riderov a sleduj výstup")
node.start()
time.sleep(30)
node.stop()
print(f"\nNájdených {len(found)} zariadení: {list(found.keys())}")
```

## Priradenie device ID k bicyklu

Po zistení ID zavolaj API:

```bash
curl -X POST http://localhost:8000/bikes/assign-strap \
  -H "Content-Type: application/json" \
  -d '{"ant_device_id": 12345, "bike_position": 1, "label": "Polar H10 #1"}'
```

Alebo cez admin panel (TODO: spraviť UI).

## Tipy

- Každý pás má jedinečné device_id vytlačené na škatuľke alebo v app
- Polar H10: ID nájdeš v Polar Flow app → zariadenie → info
- Garmin HRM: ID v Garmin Connect app
- Pri nasadení pásu mimo štúdia (doma) použiješ scan tool na bicykli 1,
  zaznačíš si ID, potom opakuješ pre každý pás
