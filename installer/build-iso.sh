#!/usr/bin/env bash
# =============================================================================
# HR Studio — build USB image s preseed
# Spusti na Linux počítači (nie na cieľovom serveri)
# Potrebuje: xorriso, isolinux, wget
#
# Použitie:
#   bash build-iso.sh
#   → vytvorí hr-studio.iso
#   → zapíše na USB: sudo dd if=hr-studio.iso of=/dev/sdX bs=4M status=progress
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[OK]${NC}   $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()     { echo -e "${RED}[ERR]${NC}  $*" >&2; exit 1; }

DEBIAN_ISO_URL="https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.4.0-amd64-netinst.iso"
DEBIAN_ISO="debian-12-netinst.iso"
OUTPUT_ISO="hr-studio.iso"
WORK_DIR="$(mktemp -d)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

cleanup() { rm -rf "$WORK_DIR"; }
trap cleanup EXIT

# ── Závislosti ────────────────────────────────────────────────────────────────
for cmd in xorriso curl; do
    command -v "$cmd" &>/dev/null || die "Chýba: $cmd  →  sudo apt install $cmd"
done

echo -e "\n${BOLD}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║   HR Studio — Build Installer ISO        ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════╝${NC}\n"

# ── Stiahni Debian ISO ────────────────────────────────────────────────────────
if [[ -f "$DEBIAN_ISO" ]]; then
    warn "Debian ISO už existuje, preskakujem download"
else
    info "Sťahujem Debian 13 netinst (~750 MB)..."
    curl -L -o "$DEBIAN_ISO" "$DEBIAN_ISO_URL"
    success "Debian ISO stiahnutý"
fi

# ── Rozbaľ ISO ────────────────────────────────────────────────────────────────
info "Rozbaľujem ISO..."
ISO_MOUNT="$WORK_DIR/iso_mount"
ISO_EXTRACT="$WORK_DIR/iso_extract"
mkdir -p "$ISO_MOUNT" "$ISO_EXTRACT"

xorriso -osirrox_verbosity 0 -indev "$DEBIAN_ISO" \
    -extract / "$ISO_EXTRACT" 2>/dev/null
success "ISO rozbalený"

# ── Vlož preseed.cfg ──────────────────────────────────────────────────────────
info "Vkladám preseed.cfg..."
cp "$SCRIPT_DIR/preseed.cfg" "$ISO_EXTRACT/preseed.cfg"
success "preseed.cfg vložený"

# ── Uprav GRUB config pre automatický boot s preseed ─────────────────────────
info "Upravujem GRUB..."
GRUB_CFG="$ISO_EXTRACT/boot/grub/grub.cfg"

# Zálohuj originál
cp "$GRUB_CFG" "${GRUB_CFG}.orig"

cat > "$GRUB_CFG" <<'EOF'
set default=0
set timeout=5

menuentry "HR Studio — Automatická inštalácia" {
    set background_color=black
    linux /install.amd/vmlinuz \
        auto=true \
        priority=critical \
        preseed/file=/cdrom/preseed.cfg \
        locale=sk_SK \
        keymap=sk \
        hostname=hrstudio \
        domain=local \
        quiet
    initrd /install.amd/initrd.gz
}

menuentry "Manuálna inštalácia Debian 12" {
    linux /install.amd/vmlinuz
    initrd /install.amd/initrd.gz
}
EOF

success "GRUB nakonfigurovaný"

# ── Uprav isolinux pre legacy BIOS boot ──────────────────────────────────────
ISOLINUX_CFG="$ISO_EXTRACT/isolinux/isolinux.cfg"
if [[ -f "$ISOLINUX_CFG" ]]; then
    cat > "$ISOLINUX_CFG" <<'EOF'
# HR Studio preseed installer
default hr-studio
timeout 50

label hr-studio
    menu label HR Studio — Automaticka instalacia
    kernel /install.amd/vmlinuz
    append auto=true priority=critical preseed/file=/cdrom/preseed.cfg \
        locale=sk_SK keymap=sk hostname=hrstudio domain=local \
        initrd=/install.amd/initrd.gz quiet

label manual
    menu label Manualna instalacia
    kernel /install.amd/vmlinuz
    append initrd=/install.amd/initrd.gz
EOF
    success "isolinux nakonfigurovaný (legacy BIOS)"
fi

# ── Zostav nový ISO ───────────────────────────────────────────────────────────
info "Zostavujem nový ISO..."

MBR_TEMPLATE="$WORK_DIR/mbr.bin"
xorriso -osirrox_verbosity 0 -indev "$DEBIAN_ISO" \
    -exec 'lsmd' -- 2>/dev/null | grep -q "isohybrid" || true

xorriso -as mkisofs \
    -r -J -joliet-long \
    -o "$OUTPUT_ISO" \
    -b isolinux/isolinux.bin \
    -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    -eltorito-alt-boot \
    -e boot/grub/efi.img \
    -no-emul-boot \
    -isohybrid-gpt-basdat \
    "$ISO_EXTRACT" 2>/dev/null \
|| \
xorriso -as mkisofs \
    -r -J -joliet-long \
    -o "$OUTPUT_ISO" \
    -b isolinux/isolinux.bin \
    -c isolinux/boot.cat \
    -no-emul-boot -boot-load-size 4 -boot-info-table \
    "$ISO_EXTRACT" 2>/dev/null

success "ISO zostavaný: $OUTPUT_ISO ($(du -sh "$OUTPUT_ISO" | cut -f1))"

# ── Výsledok ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}║              ISO je pripravený!                          ║${NC}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BOLD}Zapis na USB kľúč (min. 1 GB):${NC}"
echo -e "  ${YELLOW}sudo dd if=${OUTPUT_ISO} of=/dev/sdX bs=4M status=progress${NC}"
echo -e "  (nahraď /dev/sdX správnym diskom — skontroluj cez:  lsblk)"
echo ""
echo -e "${BOLD}Inštalácia na cieľovom počítači:${NC}"
echo -e "  1. Zasuň USB kľúč"
echo -e "  2. Boot z USB (F12 / F2 / Del na boot menu)"
echo -e "  3. Vyber 'HR Studio — Automatická inštalácia'"
echo -e "  4. Čakaj ~15 minút — všetko prebehne samo"
echo -e "  5. Počítač sa reštartuje → dashboard na TV"
echo ""
echo -e "${YELLOW}Pred produkčným použitím zmeň heslo v preseed.cfg!${NC}"
echo -e "  admin heslo: ${RED}hrstudio${NC} → zmeň na silné heslo"
echo ""
