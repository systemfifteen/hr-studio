#!/usr/bin/env bash
# =============================================================================
# HR Studio — setup script pre čistý Debian/Ubuntu server
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
INSTALL_DIR="/opt/hr-studio"
REPO_URL="https://github.com/systemfifteen/hr-studio.git"

echo -e "\n${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     HR Studio — Server Setup         ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}\n"

# ── 1. System update ──────────────────────────────────────────────────────────
info "Aktualizujem systém..."
apt-get update -qq && apt-get upgrade -y -qq
success "Systém aktualizovaný"

# ── 2. Základné balíky ────────────────────────────────────────────────────────
info "Inštalujem balíky..."
apt-get install -y -qq \
    curl git ca-certificates gnupg lsb-release \
    bluez bluetooth sqlite3
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

# ── 5. Stiahni / aktualizuj repozitár ────────────────────────────────────────
info "Sťahujem HR Studio z GitHubu..."
if [[ -d "$INSTALL_DIR/.git" ]]; then
    git -C "$INSTALL_DIR" pull --ff-only
    success "Repozitár aktualizovaný"
else
    git clone "$REPO_URL" "$INSTALL_DIR"
    success "Repozitár stiahnutý do $INSTALL_DIR"
fi
chown -R "$REAL_USER:$REAL_USER" "$INSTALL_DIR"

# ── 6. Systemd service ────────────────────────────────────────────────────────
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

# ── 7. Spustenie ──────────────────────────────────────────────────────────────
info "Budujem a spúšťam HR Studio..."
cd "$INSTALL_DIR"
docker compose -f docker-compose.standalone.yml up --build -d
success "HR Studio spustené"

# ── 8. Výsledok ───────────────────────────────────────────────────────────────
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
echo -e "  Update:    systemctl restart hr-studio   (stiahne git pull automaticky)"
echo -e "  Stop:      systemctl stop hr-studio"
echo ""
echo -e "${BOLD}BLE dongle check:${NC}"
echo -e "  hciconfig"
echo ""
