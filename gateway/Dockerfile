FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    bluez \
    dbus \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Seed DB ak ešte neexistuje
COPY seed.sql /app/seed.sql
CMD ["sh", "-c", "[ ! -f /data/local_cache.db ] && sqlite3 /data/local_cache.db < /app/seed.sql; python main.py"]
