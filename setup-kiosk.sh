#!/usr/bin/env bash
# =============================================================================
# HR Studio — kiosk setup (spusti PO setup.sh)
# TV zobrazí dashboard automaticky po boote
# Použitie: sudo bash setup-kiosk.sh
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()     { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Spusti ako root:  sudo bash setup-kiosk.sh"

KIOSK_USER="kiosk"
DASHBOARD_URL="http://localhost"

echo -e "\n${BOLD}╔══════════════════════════════════════╗${NC}"
echo -e "${BOLD}║     HR Studio — Kiosk Setup          ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════╝${NC}\n"

# ── 1. Balíky ─────────────────────────────────────────────────────────────────
info "Inštalujem X11 + Chromium..."
apt-get update -qq
apt-get install -y -qq \
    xorg openbox chromium unclutter \
    lightdm lightdm-gtk-greeter \
    x11-xserver-utils xdotool
success "Balíky nainštalované"

# ── 2. Kiosk user ─────────────────────────────────────────────────────────────
if ! id "$KIOSK_USER" &>/dev/null; then
    useradd -m -s /bin/bash "$KIOSK_USER"
    success "Používateľ '$KIOSK_USER' vytvorený"
else
    warn "Používateľ '$KIOSK_USER' už existuje"
fi

# Pridaj do skupiny video/audio pre správny prístup k displayu
usermod -aG video,audio "$KIOSK_USER"

# ── 3. LightDM autologin ──────────────────────────────────────────────────────
info "Konfigurujem autologin..."
mkdir -p /etc/lightdm
cat > /etc/lightdm/lightdm.conf <<EOF
[Seat:*]
autologin-user=${KIOSK_USER}
autologin-user-timeout=0
user-session=openbox
EOF
systemctl enable lightdm
success "LightDM autologin → $KIOSK_USER"

# ── 4. Openbox autostart ──────────────────────────────────────────────────────
info "Konfigurujem Openbox + Chromium kiosk..."
OPENBOX_DIR="/home/${KIOSK_USER}/.config/openbox"
mkdir -p "$OPENBOX_DIR"

cat > "${OPENBOX_DIR}/autostart" <<EOF
#!/bin/bash

# Vypni šetrič obrazovky a DPMS (TV nesmie zhasnúť)
xset s off &
xset -dpms &
xset s noblank &

# Schovaj kurzor myši po 1s nečinnosti
unclutter -idle 1 -root &

# Počkaj kým HR Studio naštartuje (Docker services)
sleep 8

# Spusti Chromium v kiosk móde
chromium \
    --kiosk \
    --no-first-run \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --disable-features=TranslateUI \
    --check-for-update-interval=31536000 \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    "${DASHBOARD_URL}" &

# Watchdog: ak Chromium spadne, reštartuj ho
while true; do
    sleep 30
    if ! pgrep -x chromium > /dev/null; then
        chromium \
            --kiosk \
            --no-first-run \
            --noerrdialogs \
            --disable-infobars \
            --disable-session-crashed-bubble \
            --disable-restore-session-state \
            "${DASHBOARD_URL}" &
    fi
done &
EOF

chmod +x "${OPENBOX_DIR}/autostart"
chown -R "${KIOSK_USER}:${KIOSK_USER}" "/home/${KIOSK_USER}/.config"

# ── 5. Openbox rc.xml — vypni pravý klik a klávesové skratky ─────────────────
cat > "${OPENBOX_DIR}/rc.xml" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<openbox_config xmlns="http://openbox.org/3.4/rc">
  <keyboard>
    <!-- Zakáž Alt+F4 a iné skratky -->
  </keyboard>
  <mouse>
    <context name="Root">
      <!-- Prázdne — žiadne menu na pravý klik -->
    </context>
  </mouse>
  <applications>
    <application class="*">
      <decor>no</decor>
      <maximized>yes</maximized>
    </application>
  </applications>
</openbox_config>
EOF
chown "${KIOSK_USER}:${KIOSK_USER}" "${OPENBOX_DIR}/rc.xml"

# ── 6. Zakáž Ctrl+Alt+Del reboot ─────────────────────────────────────────────
systemctl mask ctrl-alt-del.target 2>/dev/null || true
success "Ctrl+Alt+Del zakázaný"

# ── 7. Výsledok ───────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║       Kiosk nakonfigurovaný!               ║${NC}"
echo -e "${BOLD}╚════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  Po reštarte sa zobrazí dashboard na celú obrazovku."
echo -e "  URL: ${GREEN}${DASHBOARD_URL}${NC}"
echo ""
echo -e "${BOLD}Ak potrebuješ admin panel:${NC}"
echo -e "  Pripoj klávesnicu → Ctrl+Alt+T (terminál cez iný user)"
echo -e "  Alebo otvor na inom zariadení: http://$(hostname -I | awk '{print $1}')/admin.html"
echo ""
if [[ "${1:-}" == "--non-interactive" ]]; then
    info "Non-interactive režim — reboot prebehne po dokončení inštalácie"
else
    echo -e "${BOLD}Reštartuj teraz?${NC}"
    read -r -p "  [y/N] " REBOOT
    if [[ "$REBOOT" =~ ^[Yy]$ ]]; then
        reboot
    fi
fi
