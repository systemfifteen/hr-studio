#!/usr/bin/env bash
# =============================================================================
# HR Studio — setup script pre čistý Debian/Ubuntu server alebo notebook
# Použitie: sudo bash setup.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()     { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Spusti ako root:  sudo bash setup.sh"

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(eval echo "~${REAL_USER}")
INSTALL_DIR="/opt/hr-studio"
REPO_URL="https://github.com/systemfifteen/hr-studio.git"

echo -e "\n${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     HR Studio — Setup                ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}\n"

# ── Konfigurácia ──────────────────────────────────────────────────────────────
echo -e "${BOLD}Konfigurácia inštalácie:${NC}"
echo ""

read -r -p "  Notebook/kiosk setup (WiFi, helper services)? [Y/n] " OPT_NOTEBOOK
OPT_NOTEBOOK="${OPT_NOTEBOOK:-y}"

read -r -p "  Aplikovať GRUB fix pre Dell Latitude freeze (i915 PSR + NVMe)? [y/N] " OPT_GRUB
OPT_GRUB="${OPT_GRUB:-n}"

WIFI_IFACE="wlp2s0"
HOME_SSID=""; HOME_PASS=""
WORK_SSID=""; WORK_PASS=""
BACKUP_SSID=""; BACKUP_PASS=""
REVERSE_SSH_HOST=""; REVERSE_SSH_PORT="31337"; REVERSE_SSH_USER=""

if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    echo ""
    echo -e "${BOLD}WiFi konfigurácia:${NC}"
    read -r -p "  WiFi rozhranie [wlp2s0]: " WIFI_IFACE_IN
    WIFI_IFACE="${WIFI_IFACE_IN:-wlp2s0}"

    echo ""
    echo -e "  ${CYAN}Domáca sieť:${NC}"
    read -r -p "    SSID: " HOME_SSID
    read -r -s -p "    Heslo: " HOME_PASS; echo

    echo -e "  ${CYAN}Pracovná sieť (gym/štúdio):${NC}"
    read -r -p "    SSID: " WORK_SSID
    read -r -s -p "    Heslo: " WORK_PASS; echo

    echo -e "  ${CYAN}Záložná sieť (iPhone hotspot):${NC}"
    read -r -p "    SSID: " BACKUP_SSID
    read -r -s -p "    Heslo: " BACKUP_PASS; echo

    echo ""
    echo -e "${BOLD}Reverzný SSH tunel (voliteľné, Enter = preskočiť):${NC}"
    read -r -p "  Server host (napr. system15.win): " REVERSE_SSH_HOST
    if [[ -n "$REVERSE_SSH_HOST" ]]; then
        read -r -p "  SSH port servera [31337]: " REVERSE_SSH_PORT_IN
        REVERSE_SSH_PORT="${REVERSE_SSH_PORT_IN:-31337}"
        read -r -p "  SSH user na serveri: " REVERSE_SSH_USER
    fi
fi

echo ""

# ── 1. System update ──────────────────────────────────────────────────────────
info "Aktualizujem systém..."
apt-get update -qq && apt-get upgrade -y -qq
success "Systém aktualizovaný"

# ── 2. Základné balíky ────────────────────────────────────────────────────────
info "Inštalujem balíky..."
PKGS="curl git ca-certificates gnupg lsb-release bluez bluetooth sqlite3 fonts-noto-color-emoji python3"
if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    PKGS="$PKGS wpasupplicant ifupdown wireless-tools sshpass net-tools"
fi
apt-get install -y -qq $PKGS
success "Balíky nainštalované"

# ── 3. Docker ─────────────────────────────────────────────────────────────────
if command -v docker &>/dev/null; then
    warn "Docker už nainštalovaný ($(docker --version | cut -d' ' -f3 | tr -d ','))"
else
    info "Inštalujem Docker..."
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/debian/gpg \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    success "Docker nainštalovaný"
fi

if ! groups "$REAL_USER" | grep -q docker; then
    usermod -aG docker "$REAL_USER"
    success "$REAL_USER pridaný do skupiny docker"
fi

# ── 4. Bluetooth ──────────────────────────────────────────────────────────────
info "Nastavujem Bluetooth..."
systemctl enable --now bluetooth
success "Bluetooth nakonfigurovaný"

if hciconfig 2>/dev/null | grep -q "^hci"; then
    BT_IFACE=$(hciconfig 2>/dev/null | grep "^hci" | cut -d: -f1 | head -1)
    BT_MAC=$(hciconfig "$BT_IFACE" 2>/dev/null | grep "BD Address" | awk '{print $3}')
    success "BT dongle: $BT_IFACE ($BT_MAC)"
else
    warn "Žiadny BT dongle — zapoj USB dongle pred spustením HR Studia"
fi

# ── 5. WiFi setup ─────────────────────────────────────────────────────────────
if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    info "Nastavujem WiFi (wpasupplicant + ifupdown)..."

    # /etc/network/interfaces — pridaj wlp2s0 ak ešte nie je
    if ! grep -q "$WIFI_IFACE" /etc/network/interfaces 2>/dev/null; then
        cat >> /etc/network/interfaces <<EOF

allow-hotplug ${WIFI_IFACE}
iface ${WIFI_IFACE} inet dhcp
    wpa-conf /etc/wpa_supplicant/wpa_supplicant.conf
EOF
        success "Pridaný ${WIFI_IFACE} do /etc/network/interfaces"
    else
        warn "${WIFI_IFACE} už v /etc/network/interfaces"
    fi

    # Inicializuj prázdny wpa_supplicant.conf ak neexistuje
    if [[ ! -f /etc/wpa_supplicant/wpa_supplicant.conf ]]; then
        echo "ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev" \
            > /etc/wpa_supplicant/wpa_supplicant.conf
        echo "update_config=1" >> /etc/wpa_supplicant/wpa_supplicant.conf
    fi

    # WiFi skripty v home adresári
    if [[ -n "$HOME_SSID" && -n "$HOME_PASS" ]]; then
        cat > "${REAL_HOME}/wifi-home.sh" <<WSCRIPT
#!/bin/bash
# Pripojenie na domácu WiFi: ${HOME_SSID}
sudo wpa_passphrase "${HOME_SSID}" "${HOME_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
echo "Pripojené na ${HOME_SSID}"
WSCRIPT
        chmod +x "${REAL_HOME}/wifi-home.sh"
    fi

    if [[ -n "$WORK_SSID" && -n "$WORK_PASS" ]]; then
        cat > "${REAL_HOME}/wifi-studio.sh" <<WSCRIPT
#!/bin/bash
# Pripojenie na WiFi štúdia: ${WORK_SSID}
sudo wpa_passphrase "${WORK_SSID}" "${WORK_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
echo "Pripojené na ${WORK_SSID}"
WSCRIPT
        chmod +x "${REAL_HOME}/wifi-studio.sh"
    fi

    if [[ -n "$BACKUP_SSID" && -n "$BACKUP_PASS" ]]; then
        cat > "${REAL_HOME}/wifi-iphone.sh" <<WSCRIPT
#!/bin/bash
# Záložná WiFi: ${BACKUP_SSID}
sudo wpa_passphrase "${BACKUP_SSID}" "${BACKUP_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
echo "Pripojené na ${BACKUP_SSID}"
WSCRIPT
        chmod +x "${REAL_HOME}/wifi-iphone.sh"
    fi

    # wifi-auto.sh — automatický výber siete
    if [[ -n "$HOME_SSID" || -n "$WORK_SSID" ]]; then
        cat > "${REAL_HOME}/wifi-auto.sh" <<WSCRIPT
#!/bin/bash
# Auto WiFi — priorita: 1. domov, 2. štúdio, 3. záloha
echo "Skenujem dostupné siete..."
sudo ifconfig ${WIFI_IFACE} up 2>/dev/null
sleep 1
SCAN=\$(sudo iwlist ${WIFI_IFACE} scan 2>/dev/null)

if [ -n "${HOME_SSID}" ] && echo "\$SCAN" | grep -q '"${HOME_SSID}"'; then
    echo "Nájdené: ${HOME_SSID} (domov)"
    sudo wpa_passphrase "${HOME_SSID}" "${HOME_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
    sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
    echo "Pripojené na ${HOME_SSID}"
elif [ -n "${WORK_SSID}" ] && echo "\$SCAN" | grep -q '"${WORK_SSID}"'; then
    echo "Nájdené: ${WORK_SSID} (štúdio)"
    sudo wpa_passphrase "${WORK_SSID}" "${WORK_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
    sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
    echo "Pripojené na ${WORK_SSID}"
elif [ -n "${BACKUP_SSID}" ]; then
    echo "Záloha: ${BACKUP_SSID}"
    sudo wpa_passphrase "${BACKUP_SSID}" "${BACKUP_PASS}" > /etc/wpa_supplicant/wpa_supplicant.conf
    sudo ifdown ${WIFI_IFACE} 2>/dev/null; sudo ifup ${WIFI_IFACE}
    echo "Pripojené na ${BACKUP_SSID}"
else
    echo "Žiadna sieť nenájdená"
    exit 1
fi
WSCRIPT
        chmod +x "${REAL_HOME}/wifi-auto.sh"
        success "WiFi skripty vytvorené"
    fi

    # Reverzný SSH tunel
    if [[ -n "$REVERSE_SSH_HOST" && -n "$REVERSE_SSH_USER" ]]; then
        cat > "${REAL_HOME}/reverse-ssh.sh" <<RSCRIPT
#!/bin/bash
ssh -f -N -R 2222:localhost:22 -p ${REVERSE_SSH_PORT} ${REVERSE_SSH_USER}@${REVERSE_SSH_HOST}
RSCRIPT
        chmod +x "${REAL_HOME}/reverse-ssh.sh"
        success "Reverzný SSH skript: ~/reverse-ssh.sh"
    fi

    # Cron — @reboot wifi-auto + o 2:00 DB záloha
    CRON_WIFI=""
    if [[ -f "${REAL_HOME}/wifi-auto.sh" ]]; then
        CRON_WIFI="@reboot sleep 30 && ${REAL_HOME}/wifi-auto.sh >> ${REAL_HOME}/wifi-auto.log 2>&1"
    fi

    (crontab -u "$REAL_USER" -l 2>/dev/null | grep -v "wifi-auto\|hr-studio-backup"; \
     [[ -n "$CRON_WIFI" ]] && echo "$CRON_WIFI"; \
     echo "0 2 * * * ${REAL_HOME}/backups/hr-studio-backup.sh >> ${REAL_HOME}/backups/hr-studio-backup.log 2>&1") \
    | crontab -u "$REAL_USER" -
    success "Cron nakonfigurovaný (@reboot wifi-auto, 02:00 DB záloha)"

    chown "${REAL_USER}:${REAL_USER}" ${REAL_HOME}/wifi-*.sh ${REAL_HOME}/reverse-ssh.sh 2>/dev/null || true
fi

# ── 6. Helper services ────────────────────────────────────────────────────────
if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    info "Inštalujem helper services (HDMI, BT, Poweroff)..."

    # hdmi-helper.py
    cat > "${REAL_HOME}/hdmi-helper.py" <<'PYEOF'
#!/usr/bin/env python3
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/fix-hdmi':
            result = subprocess.run(
                ['sudo', '-u', 'kiosk',
                 'env', 'DISPLAY=:0', 'XAUTHORITY=/home/kiosk/.Xauthority',
                 'xrandr', '--output', 'HDMI-1',
                 '--mode', '1920x1080', '--rate', '60', '--same-as', 'eDP-1'],
                capture_output=True, text=True
            )
            ok = result.returncode == 0
            self.send_response(200 if ok else 500)
            self.end_headers()
            self.wfile.write(b'ok' if ok else result.stderr.encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args): pass

print('HDMI helper listening on 127.0.0.1:8767')
HTTPServer(('127.0.0.1', 8767), Handler).serve_forever()
PYEOF

    # bt-helper.py
    cat > "${REAL_HOME}/bt-helper.py" <<'PYEOF'
#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/restart-bt':
            try:
                subprocess.run(['systemctl', 'restart', 'bluetooth'], check=True, timeout=15)
                logging.info('bluetooth restarted OK')
                self.send_response(200)
            except Exception as e:
                logging.error('BT restart failed: %s', e)
                self.send_response(500)
        else:
            self.send_response(404)
        self.end_headers()
    def log_message(self, *a): pass

HTTPServer(('127.0.0.1', 8768), Handler).serve_forever()
PYEOF

    # poweroff-helper.py
    cat > "${REAL_HOME}/poweroff-helper.py" <<'PYEOF'
#!/usr/bin/env python3
from http.server import BaseHTTPRequestHandler, HTTPServer
import subprocess, logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/poweroff':
            try:
                logging.info('Poweroff requested via HR Studio admin')
                self.send_response(200)
                self.end_headers()
                subprocess.Popen(['systemctl', 'poweroff'])
            except Exception as e:
                logging.error('Poweroff failed: %s', e)
                self.send_response(500)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, *a): pass

print('Poweroff helper listening on 127.0.0.1:8769')
HTTPServer(('127.0.0.1', 8769), Handler).serve_forever()
PYEOF

    chown "${REAL_USER}:${REAL_USER}" \
        "${REAL_HOME}/hdmi-helper.py" \
        "${REAL_HOME}/bt-helper.py" \
        "${REAL_HOME}/poweroff-helper.py"

    # Systemd units
    cat > /etc/systemd/system/hdmi-helper.service <<EOF
[Unit]
Description=HR Studio HDMI Helper
After=network.target

[Service]
Type=simple
User=${REAL_USER}
ExecStart=/usr/bin/python3 ${REAL_HOME}/hdmi-helper.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/bt-helper.service <<EOF
[Unit]
Description=BT Restart Helper for HR Studio
After=network.target

[Service]
ExecStart=/usr/bin/python3 ${REAL_HOME}/bt-helper.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

    cat > /etc/systemd/system/poweroff-helper.service <<EOF
[Unit]
Description=Poweroff Helper for HR Studio
After=network.target

[Service]
ExecStart=/usr/bin/python3 ${REAL_HOME}/poweroff-helper.py
Restart=always
User=root

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable --now hdmi-helper bt-helper poweroff-helper
    success "Helper services: hdmi-helper (8767), bt-helper (8768), poweroff-helper (8769)"

    # Sudoers — xrandr pre kiosk user cez adminhrstudio
    if [[ ! -f /etc/sudoers.d/hdmi-fix ]]; then
        echo "${REAL_USER} ALL=(kiosk) NOPASSWD: /usr/bin/xrandr" \
            > /etc/sudoers.d/hdmi-fix
        chmod 440 /etc/sudoers.d/hdmi-fix
        success "Sudoers: xrandr pre kiosk user"
    fi
fi

# ── 7. GRUB fix (Dell Latitude i915 PSR + NVMe APST) ─────────────────────────
if [[ "$OPT_GRUB" =~ ^[Yy]$ ]]; then
    info "Aplikujem GRUB fix pre Dell Latitude (i915 PSR + NVMe)..."
    GRUB_FILE="/etc/default/grub"
    if grep -q "i915.enable_psr=0" "$GRUB_FILE"; then
        warn "GRUB fix už aplikovaný"
    else
        sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="[^"]*"/GRUB_CMDLINE_LINUX_DEFAULT="quiet i915.enable_psr=0 nvme_core.default_ps_max_latency_us=0"/' "$GRUB_FILE"
        update-grub
        success "GRUB fix aplikovaný (účinný po reštarte)"
    fi
fi

# ── 8. DB záloha ──────────────────────────────────────────────────────────────
if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    info "Nastavujem DB zálohu..."
    mkdir -p "${REAL_HOME}/backups/hr-studio"
    cat > "${REAL_HOME}/backups/hr-studio-backup.sh" <<'BSCRIPT'
#!/bin/bash
BACKUP_DIR="/home/adminhrstudio/backups/hr-studio"
VOLUME="hr-studio_cache_data"
DATE=$(date +%Y-%m-%d_%H-%M)
DEST="$BACKUP_DIR/local_cache_$DATE.db"

mkdir -p "$BACKUP_DIR"
docker run --rm -v "$VOLUME":/data alpine sh -c "cat /data/local_cache.db" > "$DEST"

if [ $? -eq 0 ] && [ -s "$DEST" ]; then
    echo "$(date): záloha OK → $DEST ($(du -h "$DEST" | cut -f1))"
    ls -t "$BACKUP_DIR"/local_cache_*.db | tail -n +8 | xargs -r rm
else
    echo "$(date): záloha ZLYHALA"
    rm -f "$DEST"
    exit 1
fi
BSCRIPT
    # Oprav cestu k REAL_USER v backup skripte
    sed -i "s|/home/adminhrstudio|${REAL_HOME}|g" "${REAL_HOME}/backups/hr-studio-backup.sh"
    chmod +x "${REAL_HOME}/backups/hr-studio-backup.sh"
    chown -R "${REAL_USER}:${REAL_USER}" "${REAL_HOME}/backups"
    success "DB záloha: ${REAL_HOME}/backups/hr-studio-backup.sh (cron 02:00)"
fi

# ── 9. Stiahni / aktualizuj repozitár ────────────────────────────────────────
info "Sťahujem HR Studio z GitHubu..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
    success "Repozitár aktualizovaný"
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    success "Repozitár stiahnutý do $INSTALL_DIR"
fi
chown -R "${REAL_USER}:${REAL_USER}" "$INSTALL_DIR"

# ── 10. Systemd service ───────────────────────────────────────────────────────
info "Vytváram systemd service..."
cat > /etc/systemd/system/hr-studio.service <<EOF
[Unit]
Description=HR Studio
After=network.target docker.service bluetooth.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=$INSTALL_DIR
ExecStartPre=git -C $INSTALL_DIR pull --ff-only
ExecStart=docker compose -f docker-compose.standalone.yml up --build
ExecStop=docker compose -f docker-compose.standalone.yml down
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable hr-studio
success "Systemd service hr-studio.service vytvorený a povolený"

# ── 11. Spustenie ─────────────────────────────────────────────────────────────
info "Budujem a spúšťam HR Studio..."
cd "$INSTALL_DIR"
docker compose -f docker-compose.standalone.yml up --build -d
success "HR Studio spustené"

# ── 12. Výsledok ──────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║        HR Studio je spustené!            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Dashboard:   ${GREEN}http://${SERVER_IP}${NC}"
echo -e "  Admin panel: ${GREEN}http://${SERVER_IP}/admin.html${NC}"
echo ""
echo -e "${BOLD}Užitočné príkazy:${NC}"
echo -e "  Logy:      journalctl -u hr-studio -f"
echo -e "  Reštart:   systemctl restart hr-studio"
echo -e "  Update:    cd $INSTALL_DIR && git pull && docker compose -f docker-compose.standalone.yml build && docker compose -f docker-compose.standalone.yml up -d"
echo -e "  Stop:      systemctl stop hr-studio"
echo ""
if [[ "$OPT_NOTEBOOK" =~ ^[Yy]$ ]]; then
    echo -e "${BOLD}WiFi:${NC}"
    echo -e "  Auto:    ~/wifi-auto.sh   (spúšťa sa aj pri boote)"
    [[ -n "$HOME_SSID" ]]   && echo -e "  Domov:   ~/wifi-home.sh"
    [[ -n "$WORK_SSID" ]]   && echo -e "  Štúdio:  ~/wifi-studio.sh"
    [[ -n "$BACKUP_SSID" ]] && echo -e "  Záloha:  ~/wifi-iphone.sh"
    echo ""
    echo -e "${BOLD}Ďalší krok — kiosk setup:${NC}"
    echo -e "  sudo bash $INSTALL_DIR/setup-kiosk.sh"
    echo ""
fi
echo -e "${BOLD}BLE dongle check:${NC}"
echo -e "  hciconfig"
echo ""
