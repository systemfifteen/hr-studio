#!/usr/bin/env bash
# =============================================================================
# HR Studio — setup script pre čistý Debian/Ubuntu server
# Použitie: sudo bash setup.sh
# =============================================================================
set -euo pipefail

# ── Farby ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()     { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

# ── Root check ────────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "Spusti ako root:  sudo bash setup.sh"

REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME=$(getent passwd "$REAL_USER" | cut -d: -f6)

echo -e "\n${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     HR Studio — Server Setup         ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}\n"

# ── 1. System update ──────────────────────────────────────────────────────────
info "Aktualizujem systém..."
apt-get update -qq
apt-get upgrade -y -qq
success "Systém aktualizovaný"

# ── 2. Základné balíky ────────────────────────────────────────────────────────
info "Inštalujem základné balíky..."
apt-get install -y -qq \
    curl wget git ca-certificates gnupg lsb-release \
    bluez bluetooth sqlite3 ufw \
    apt-transport-https software-properties-common
success "Základné balíky nainštalované"

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
        https://download.docker.com/linux/debian \
        $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    success "Docker nainštalovaný"
fi

# Pridaj usera do docker skupiny
if ! groups "$REAL_USER" | grep -q docker; then
    usermod -aG docker "$REAL_USER"
    success "$REAL_USER pridaný do skupiny docker (platí po novom prihlásení)"
fi

# ── 4. Bluetooth ──────────────────────────────────────────────────────────────
info "Nastavujem Bluetooth..."
systemctl enable bluetooth
systemctl start bluetooth
# Povolíme BLE scanning pre non-root (potrebuje D-Bus prístup)
if ! grep -q "hr-studio" /etc/dbus-1/system.d/bluetooth.conf 2>/dev/null; then
    cat >> /etc/dbus-1/system.d/bluetooth.conf <<'EOF'
  <!-- HR Studio: allow hr-studio containers BLE access via D-Bus -->
EOF
fi
success "Bluetooth nakonfigurovaný"

# Skontroluj či je nejaký BT dongle prítomný
if hciconfig 2>/dev/null | grep -q "hci"; then
    BT_IFACE=$(hciconfig 2>/dev/null | grep "^hci" | cut -d: -f1 | head -1)
    BT_MAC=$(hciconfig "$BT_IFACE" 2>/dev/null | grep "BD Address" | awk '{print $3}')
    success "BT dongle nájdený: $BT_IFACE ($BT_MAC)"
else
    warn "Žiadny BT dongle nenájdený — zapoj USB dongle a spusti:  hciconfig"
fi

# ── 5. Firewall ───────────────────────────────────────────────────────────────
info "Nastavujem UFW firewall..."
ufw --force reset > /dev/null
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp   # HTTP  (Coolify proxy)
ufw allow 443/tcp  # HTTPS (Coolify proxy)
ufw allow 8000/tcp # Coolify dashboard
ufw --force enable
success "Firewall nakonfigurovaný (22, 80, 443, 8000)"

# ── 6. Coolify ────────────────────────────────────────────────────────────────
if docker ps 2>/dev/null | grep -q coolify; then
    warn "Coolify už beží"
elif curl -s http://localhost:8000 &>/dev/null; then
    warn "Coolify already accessible on port 8000"
else
    info "Inštalujem Coolify..."
    curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
    success "Coolify nainštalovaný"
fi

# ── 7. Systémový servis pre D-Bus socket (BLE v Dockeri) ─────────────────────
info "Konfigurujem D-Bus socket pre BLE v kontajneroch..."
# /run/dbus je mountovaný do gateway kontajnera (privileged)
# Uistime sa že dbus-daemon beží
if ! systemctl is-active --quiet dbus; then
    systemctl enable --now dbus
fi
success "D-Bus OK"

# ── 8. Výsledok ───────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║              Setup dokončený!                            ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Systém je pripravený. Ďalšie kroky:${NC}"
echo ""
echo -e "${BOLD}1. Otvor Coolify dashboard:${NC}"
echo -e "   http://${SERVER_IP}:8000"
echo ""
echo -e "${BOLD}2. Pridaj GitHub repozitár v Coolify:${NC}"
echo -e "   Repository:  git@github.com:systemfifteen/hr-studio.git"
echo -e "   Branch:      main"
echo -e "   Build Pack:  Docker Compose"
echo -e "   Compose file: docker-compose.frontend.yml"
echo ""
echo -e "${BOLD}3. Nastav doménu v Coolify:${NC}"
echo -e "   Domain: hrstudio.yourdomain.com"
echo -e "   → Coolify automaticky vygeneruje SSL certifikát"
echo ""
echo -e "${BOLD}4. Pridaj Environment Variables v Coolify:${NC}"
echo -e "   LOCAL_CACHE_DB=/data/local_cache.db"
echo -e "   WS_PORT=8765"
echo -e "   GATEWAY_RELOAD_PORT=8766"
echo ""
echo -e "${BOLD}5. Skontroluj BT dongle:${NC}"
echo -e "   hciconfig"
echo -e "   bluetoothctl -- show"
echo ""
echo -e "${YELLOW}Poznámka: odhlásiť a znova prihlásiť pre docker group.${NC}"
echo ""
