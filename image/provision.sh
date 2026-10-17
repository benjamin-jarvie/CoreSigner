#!/bin/bash
# Core Signer dev-image step 2 of 2 — run ON THE PI over SSH (or HDMI+keyboard):
#   sudo bash /boot/firmware/coresigner-provision.sh
#
# Idempotent: safe to re-run. Builds the DEV image (SSH stays on).
# The RELEASE image hardening (network stack removal, read-only root,
# reproducible build) is M3 work and deliberately not here.
set -euo pipefail

BOOT=/boot/firmware
PINS="$BOOT/coresigner-PINS"
[ -f "$PINS" ] || { echo "coresigner-PINS missing from $BOOT — run prepare-sd.sh first"; exit 1; }
# shellcheck disable=SC1090
source "$PINS"

echo "== 1/5 Bitcoin Core $CORE_VERSION"
if ! command -v bitcoind >/dev/null || ! bitcoind --version | grep -q "v$CORE_VERSION"; then
    if [ "$CORE_SHA256" = "UNPINNED_UNTIL_FIRST_FLASH" ]; then
        # Refuse before downloading. The hash has to come from a trusted
        # machine anyway: SHA256SUMS served by the host that served the
        # binary proves nothing, so there is nothing useful to learn here.
        echo "!! CORE_SHA256 is unpinned, refusing to install Bitcoin Core."
        echo "!! On a trusted machine, verify the tarball against SHA256SUMS"
        echo "!! and the builder GPG keys from the guix.sigs repo, then record"
        echo "!! the hash in image/PINS and re-run."
        exit 1
    fi
    # NOT /tmp. On Trixie /tmp is a tmpfs sized at half of RAM, which is
    # 208MB on a Zero 2 W. The tarball is 82MB and unpacks to more than
    # that, so the extract dies part-written, and filling that tmpfs eats
    # the very RAM the M0 gate exists to measure. Work on disk instead.
    work="$(mktemp -d /var/tmp/coresigner-core.XXXXXX)"
    trap 'rm -rf "$work"' EXIT
    curl -fSL -o "$work/$CORE_TARBALL" "$CORE_URL"
    echo "$CORE_SHA256  $work/$CORE_TARBALL" | sha256sum -c -
    # Only the two binaries Core Signer runs, and the tarball goes with
    # the temp directory below. bitcoin-qt has never been installed on a
    # card: `git log -S "bin/bitcoin-qt" -- image/provision.sh` is empty.
    #
    # It is in the download because Core publishes ONE Linux archive per
    # platform, holding the node, the GUI, the CLI and the tools, with no
    # GUI-less variant to ask for. The sha256 we verify is the whole
    # archive's, so all 178.6MB is fetched to check the 19MB we keep.
    # bitcoin-qt and libexec/bitcoin-gui are 87.3MB of it and
    # libexec/test_bitcoin another 32.2MB (measured 2026-10-16); none of
    # the three is ever executed here.
    tar xzf "$work/$CORE_TARBALL" -C "$work" \
        "bitcoin-$CORE_VERSION/bin/bitcoind" "bitcoin-$CORE_VERSION/bin/bitcoin-cli"
    install -m 755 "$work/bitcoin-$CORE_VERSION/bin/bitcoind" \
                   "$work/bitcoin-$CORE_VERSION/bin/bitcoin-cli" /usr/local/bin/
    rm -rf "$work"
    trap - EXIT
fi
bitcoind --version | head -1

echo "== 2/5 system packages"
apt-get update -qq
# Every package the device needs, each installed on its own line, so a
# name that does not exist in this release cannot take another package
# down with it. The old form asked for seven at once and fell back to a
# list that quietly dropped python3-picamera2, so an unavailable
# python3-zbar would have cost the camera and said nothing until a scan
# failed on the board.
# libzbar0: pyzbar (in image/requirements.txt) dlopens it at import;
# pip cannot provide it.
# FROM PINNED FILES, NOT FROM THE INDEX. `apt-get install` takes
# whatever the archive is serving today, so two cards provisioned a week
# apart carried different software and neither could be reproduced from
# anything in this repository. The six URLs and their hashes are in
# PINS; nothing here reaches an index.
#
# Measured on the board 2026-09-23: this list resolves to exactly six
# packages, because the 958 the OS image carries satisfy every
# dependency. If that stops being true, `dpkg -i` fails on a missing
# dependency and says which, which is the loud failure we want.
DEB_CACHE="$(mktemp -d)"
trap 'rm -rf "$DEB_CACHE"' EXIT
echo "$PINNED_DEBS" | while read -r url want size; do
    [ -n "$url" ] || continue
    file="$DEB_CACHE/$(basename "$url")"
    curl -sSfL --retry 3 -o "$file" "$url" || {
        echo "!! could not fetch $url"
        echo "!! The Raspberry Pi archive is rolling and drops old"
        echo "!! versions. A release carries its own copies; see PINS."
        exit 1
    }
    got_size=$(stat -c %s "$file")
    [ "$got_size" = "$size" ] || {
        echo "!! $(basename "$url") is $got_size bytes, pinned at $size"
        exit 1
    }
    got=$(sha256sum "$file" | cut -d" " -f1)
    [ "$got" = "$want" ] || {
        echo "!! $(basename "$url") hashes $got"
        echo "!! PINS says                    $want"
        echo "!! Refusing to install it."
        exit 1
    }
done || exit 1
dpkg -i "$DEB_CACHE"/*.deb || {
    echo "!! a pinned package did not install. Stopping."
    echo "!! If dpkg names a missing dependency, the OS image no longer"
    echo "!! carries it and PINS needs that package added."
    exit 1
}
# python3-zbar is deliberately absent. pyzbar comes from pip and only
# needs libzbar0, which is pinned above, so the old opportunistic
# `apt-get install python3-zbar || true` was reaching an unpinned index
# for something nothing uses.
echo "== 3/5 coresigner -> /opt/coresigner"
# VERIFY THE PAYLOAD FIRST. Bitcoin Core is checked against a sha256 and
# eleven GPG signatures a few lines above; the signer's own code was the
# one thing this script took on trust, unpacked as root, and then ran as
# root at every boot. prepare-sd.sh records the hash beside the tarball
# (audit of image/, 2026-09-08).
if [ -z "${CORESIGNER_TARBALL_SHA256:-}" ]; then
    echo "!! coresigner-PINS carries no CORESIGNER_TARBALL_SHA256."
    echo "!! Re-run image/prepare-sd.sh to write the card; a payload this"
    echo "!! script cannot check is a payload it will not unpack as root."
    exit 1
fi
echo "$CORESIGNER_TARBALL_SHA256  $BOOT/coresigner.tar.gz" | sha256sum -c - || {
    echo "!! coresigner.tar.gz does not match the hash on this card."
    echo "!! Either the card was written twice, or somebody changed it."
    exit 1
}
rm -rf /opt/coresigner      # stale files from a prior provision must not survive
mkdir -p /opt/coresigner
# --no-same-owner: everything belongs to root, whatever the tarball says.
tar xzf "$BOOT/coresigner.tar.gz" -C /opt/coresigner --no-same-owner
cp "$BOOT/coresigner-PINS" /opt/coresigner/PINS.installed

# pip LAST, and from the payload, in that order on purpose. The lock file
# ships inside coresigner.tar.gz, so it can only be trusted after the tarball
# has been checked against its hash directly above. Installing from it
# before unpacking would have read a file that was not there yet, which
# is what the first version of this change did.
#
# Pinned by CONTENT, not just by version: --require-hashes refuses any
# wheel whose sha256 does not match, so a compromised index cannot hand
# this device a different qrcode under the same version number. Core has
# been pinned this way since the beginning; these four were version-only
# until the image/ audit on 2026-09-08.
echo "   pip, hash-pinned"
python3 -m pip install --quiet --break-system-packages \
    --require-hashes -r /opt/coresigner/image/requirements.txt

# WHAT THIS CARD WAS BUILT FROM, for the splash to draw and a tester to
# quote in a report. Hashed by the OS and not by us: nothing in
# coresigner/ may compute a digest (PLAN A-22), and the splash only
# reads the string back.
tree_hash="$(find /opt/coresigner/coresigner -name "*.py" -type f -print0 \
             | sort -z | xargs -0 sha256sum | sha256sum | cut -c1-12)"
build_id="${CORESIGNER_COMMIT:-unknown} $tree_hash"
printf "%s\n" "$build_id" > /opt/coresigner/BUILD
echo "   build $build_id"

echo "== 4/5 ramdisk datadir + bitcoin.conf"
mkdir -p /run/coresigner
grep -q "coresigner-ramdisk" /etc/fstab || \
    echo "tmpfs /run/coresigner tmpfs rw,nosuid,nodev,size=128m,mode=0700 0 0 # coresigner-ramdisk" >> /etc/fstab
mount /run/coresigner 2>/dev/null || true
install -m 644 /opt/coresigner/m0/bitcoin.conf /etc/coresigner-bitcoin.conf

echo "== 5/5 systemd units (installed, NOT enabled on the dev image)"
install -m 644 "$BOOT/coresigner.service" /etc/systemd/system/coresigner.service
install -m 644 "$BOOT/coresigner-bitcoind.service" /etc/systemd/system/coresigner-bitcoind.service
install -m 644 "$BOOT/coresigner-splash.service" /etc/systemd/system/coresigner-splash.service
# The USB PSBT channel mounts itself (map e2e-before-testers, ticket 15).
install -m 644 /opt/coresigner/image/coresigner-usb@.service /etc/systemd/system/coresigner-usb@.service
install -m 644 /opt/coresigner/image/99-coresigner-usb.rules /etc/udev/rules.d/99-coresigner-usb.rules
udevadm control --reload-rules || true
# The USB PSBT channel's mount point. A stick that is plugged in mounts
# itself here through the udev rule above; until one is, it is empty.
mkdir -p /mnt/usb
systemctl daemon-reload
echo "   enable boot-to-coresigner with: sudo systemctl enable --now coresigner"

CFG0="${CFG0:-/boot/firmware/config.txt}"
[ -f "$CFG0" ] || CFG0=/boot/config.txt

echo "== nothing writes secrets to the card, and no console leaves the board"
# SWAP. This is the worst leak path on the device and it was open until
# 2026-09-05. Raspberry Pi OS enables swap by default, so the Python heap
# holding a key can be paged to the SD card, which defeats the whole
# "nothing persists" claim. m0/FLASH.md told the operator to run
# `swapoff -a` by hand for the gate, and noted it comes back at reboot.
# A signer must never have swap at all.
systemctl disable --now dphys-swapfile 2>/dev/null || true
systemctl mask dphys-swapfile 2>/dev/null || true
apt-get purge -y -qq dphys-swapfile 2>/dev/null || true
# Trixie: systemd-zram-generator owns dev-zram0.swap and recreates it.
systemctl mask dev-zram0.swap swap.target 2>/dev/null || true
rm -f /etc/systemd/zram-generator.conf
printf '[swap]\nzram-size = 0\n' > /etc/systemd/zram-generator.conf
swapoff -a 2>/dev/null || true
sed -i '/\sswap\s/d' /etc/fstab 2>/dev/null || true

# THE JOURNAL. Core quotes keys back in its errors and Core Signer redacts them,
# but a journal on the card is still a permanent record of a session.
# Keep it in RAM, where it dies with the power like everything else.
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/coresigner-volatile.conf <<'JEOF'
[Journal]
Storage=volatile
RuntimeMaxUse=16M
JEOF
rm -rf /var/log/journal

# THE SERIAL CONSOLE. Raspberry Pi OS puts a login console on GPIO pins 8
# and 10 by default. Three wires and physical access is a root shell on a
# device holding a key. The panel is the only interface this device has.
CMDLINE="${CMDLINE:-/boot/firmware/cmdline.txt}"
[ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
if [ -f "$CMDLINE" ]; then
    sed -i 's/console=serial0,[0-9]*//g; s/console=ttyAMA0,[0-9]*//g; s/  */ /g' "$CMDLINE"
fi
systemctl disable --now serial-getty@ttyAMA0.service serial-getty@ttyS0.service 2>/dev/null || true
systemctl mask serial-getty@ttyAMA0.service serial-getty@ttyS0.service 2>/dev/null || true
grep -q "^enable_uart=0" "$CFG0" 2>/dev/null || echo "enable_uart=0" >> "$CFG0"

# USB. The Zero's port can be a HOST or a DEVICE. As a device it can
# enumerate to a computer as a network card, a serial port or a disk, all
# of which are ways off this board. Force host mode and refuse the gadget
# drivers.
grep -q "^dtoverlay=dwc2,dr_mode=host" "$CFG0" || \
    echo "dtoverlay=dwc2,dr_mode=host" >> "$CFG0"
cat > /etc/modprobe.d/coresigner-no-gadget.conf <<'GEOF'
# Core Signer: this device is a USB host. It is never a USB device.
blacklist g_ether
blacklist g_serial
blacklist g_mass_storage
blacklist g_multi
blacklist libcomposite
blacklist usb_f_ecm
blacklist usb_f_rndis
GEOF

# The radios, the serial console and SSH are NOT touched here. This script
# builds the DEV image, and the dev image keeps them so you can work on the
# board over the network. Taking them away is image/harden.sh, which is a
# one-way step you run when the device is about to hold a real key.
echo "== dev image: radios and SSH stay. Run image/harden.sh before real keys."

# SPI for the display hat
raspi-config nonint do_spi 0 || true

# Core Signer is headless: the panel is a 320x240 ST7789 on SPI, driven by
# hw/vendor/st7789.py, so the firmware's GPU split buys nothing.
#
# Measured on a Zero 2 W, 2026-09-03, one reboot per row:
#
#   config                    total  avail  vc_sm    err lines
#   stock (vc4 on, split 64)   415    276   OK        0
#   vc4 OFF, split 64          414    281   OK        0
#   vc4 on,  split 32          447    307   OK        0
#   vc4 off, split 32          446    309   OK        0
#   vc4 off, split 48          430    294   OK        0
#   vc4 off, split 16          462    334   BROKEN    5
#
# Two things that table settles. Disabling the vc4 KMS overlay gains
# nothing at all (414 against 415), so it is left alone and the HDMI
# console keeps working. And 16 is below the floor: the VideoCore
# services die with "vc_sm_cma_vchi_init: failed to open VCHI service",
# which takes bcm2835_isp with them, and that is the ISP libcamera uses.
# 32 is the smallest split that keeps them healthy.
# CFG0, computed above with a fallback to the old boot layout. This block
# used to define its own CFG=/boot/firmware/config.txt with no fallback,
# so on a board using /boot/config.txt the `cp -n` below failed and
# `set -e` killed provisioning at the last step (audit of image/,
# 2026-09-08).
CFG="$CFG0"
if ! grep -q "^gpu_mem=" "$CFG"; then
    cp -n "$CFG" "$CFG.pre-coresigner"     # one file to put the stock split back
    printf '\n# Core Signer: headless signer. 32 is the floor; 16 starves the\n# VideoCore services that bcm2835_isp needs.\ngpu_mem=32\n' >> "$CFG"
    echo "   gpu_mem=32, +32MB of RAM (takes effect on reboot)"
fi

# Boot straight to the splash. Both of these are in SeedSigner OS's own
# config.txt for this exact board (seedsigner-os, opt/pi02w/board/
# boot_config.txt, read 2026-09-08), so they are proven on a Zero 2 W
# rather than guessed at. boot_delay defaults to 1 second and buys
# nothing on a board with one boot device; disable_splash removes the
# firmware rainbow, which on Core Signer is a frame of somebody else's branding
# before splash.py paints ours.
#
# NOT MEASURED HERE. Core Signer's boot time has never been recorded, so the
# saving is "one second plus the rainbow" by arithmetic and not by a
# stopwatch. Record it on the first hardened boot.
for kv in "boot_delay=0" "disable_splash=1"; do
    grep -q "^${kv%%=*}=" "$CFG" || printf '%s\n' "$kv" >> "$CFG"
done

echo
echo "PROVISION DONE (dev image). Sanity check:"
echo "  the suites do not ship; run them from a clone on the dev machine"
