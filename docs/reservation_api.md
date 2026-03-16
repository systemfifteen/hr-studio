# API kontrakt — rezervačný systém → HR monitor

## Čo treba pridať do rezervačného systému

### Rozšírenie rider modelu

```python
# Nové polia na rider/user modeli v rezervačnom systéme
birth_year       = Column(Integer, nullable=False)         # rok narodenia
weight_kg        = Column(Float,   nullable=True)          # váha v kg (voliteľné)
gender           = Column(String(1), nullable=True)        # 'M' alebo 'F' (voliteľné)
max_hr_override  = Column(Integer, nullable=True)          # ak rider pozná reálne MEP

# Na rezervácii
bike_number      = Column(Integer, nullable=True)          # číslo bicykla 1–20
```

### MEP výpočet (na strane HR monitora)
HR monitor si MEP dopočíta sám — netreba ho ukladať v rezervačnom systéme.
Ak rider pozná svoje reálne MEP z testovania, môže ho zadať ako `max_hr_override`.

---

## Endpoint: GET /api/v1/studio/riders/today

Vráti všetkých potvrdených riderov pre daný deň.

**Autentifikácia:** `X-Studio-Key: <STUDIO_API_KEY>` header

**Query params:**
- `date` (optional) — `YYYY-MM-DD`, default = dnes

**Response 200:**
```json
[
  {
    "reservation_id": 142,
    "bike_position":  3,
    "name":           "Ján Novák",
    "birth_year":     1985,
    "weight_kg":      78.5,
    "gender":         "M",
    "max_hr_override": null
  },
  {
    "reservation_id": 143,
    "bike_position":  7,
    "name":           "Jana Kováčová",
    "birth_year":     1992,
    "weight_kg":      null,
    "gender":         "F",
    "max_hr_override": 178
  }
]
```

**Response 403:** Nesprávny API kľúč
**Response 200 (prázdne pole):** Žiadne rezervácie na daný deň

---

## Webhook: POST na HR monitor

Keď sa zmení rezervácia, cloud pošle notifikáciu na lokálny HR monitor.

**Endpoint na HR monitore:** `POST /webhook/reservation-change`

**Header:** `X-Webhook-Secret: <WEBHOOK_SECRET>`

**Body:**
```json
{
  "event":      "created",
  "rider_name": "Ján Novák",
  "bike":       3,
  "date":       "2026-03-16"
}
```

**Events:** `created`, `updated`, `cancelled`

HR monitor po prijatí webhooknu triggerne okamžitý sync.
Webhook je best-effort — ak HR monitor nie je dostupný, nevadí.

---

## Príklad implementácie webhook odosielania v rezervačnom systéme

```python
import httpx
import os

STUDIO_WEBHOOK_URL    = os.getenv("STUDIO_WEBHOOK_URL", "")
STUDIO_WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

async def notify_hr_monitor(event: str, reservation):
    if not STUDIO_WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            await client.post(
                f"{STUDIO_WEBHOOK_URL}/webhook/reservation-change",
                json={
                    "event":      event,
                    "rider_name": reservation.rider.name,
                    "bike":       reservation.bike_number,
                    "date":       str(reservation.date),
                },
                headers={"X-Webhook-Secret": STUDIO_WEBHOOK_SECRET},
            )
    except Exception:
        pass   # webhook je voliteľný, nikdy neblokuje hlavnú logiku
```

Zavolaj `notify_hr_monitor()` v eventoch: `reservation.created`,
`reservation.updated`, `reservation.cancelled`.
