#!/bin/sh
# install.sh -- apply the OEA NAS configuration on a fresh Armbian install.
#
# Run as root on the board:
#     sh install.sh
#
# What it does:
#   1. disables NetworkManager + systemd-resolved (they fight netup.sh)
#   2. installs /usr/local/sbin/netup.sh + netup.service
#   3. sets hostname to oeanas and enables mDNS (avahi) + WSD (wsdd2)
#   4. fixes /etc/resolv.conf
#   5. enables samba
#
# It does NOT touch /etc/fstab or create the mergerfs pool -- do that by hand,
# see docs/09-storage.md.

set -e

if [ "$(id -u)" != "0" ]; then
    echo "run as root" >&2
    exit 1
fi

HERE=$(cd "$(dirname "$0")" && pwd)

echo "=== 1/6 disable NetworkManager and systemd-resolved ==="
systemctl disable --now NetworkManager 2>/dev/null || true
rm -f /etc/NetworkManager/system-connections/*.nmconnection 2>/dev/null || true
rm -f /etc/NetworkManager/conf.d/99-allow-ethernet.conf 2>/dev/null || true
systemctl disable --now systemd-resolved 2>/dev/null || true

echo "=== 2/6 install netup.sh + netup.service ==="
install -m 0755 "$HERE/netup.sh"      /usr/local/sbin/netup.sh
install -m 0644 "$HERE/netup.service" /etc/systemd/system/netup.service
systemctl daemon-reload
systemctl enable netup.service
systemctl restart netup.service

echo "=== 3/6 hostname + mDNS + WSD ==="
hostnamectl set-hostname oeanas || true
if grep -q '^127.0.1.1' /etc/hosts; then
    sed -i 's/^127\.0\.1\.1.*/127.0.1.1\toeanas/' /etc/hosts
else
    echo "127.0.1.1	oeanas" >> /etc/hosts
fi

apt-get install -y --no-install-recommends avahi-daemon libnss-mdns wsdd2 || \
    echo "WARN: avahi/wsdd2 install failed (no network?)"

systemctl enable --now avahi-daemon 2>/dev/null || true
systemctl enable --now wsdd2        2>/dev/null || true

echo "=== 4/6 /etc/resolv.conf ==="
# systemd-resolved is off, so resolv.conf must be a real file
rm -f /etc/resolv.conf
cat > /etc/resolv.conf <<'EOF'
# OEA NAS - plain resolv.conf (systemd-resolved is disabled)
nameserver 192.168.101.1
nameserver 223.5.5.5
nameserver 119.29.29.29
options edns0 trust-ad timeout:2 attempts:2
EOF
chmod 644 /etc/resolv.conf

echo "=== 5/6 samba ==="
if command -v smbd >/dev/null 2>&1; then
    systemctl enable --now smbd nmbd
else
    echo "samba not installed yet -- run:"
    echo "  apt-get install -y --no-install-recommends samba samba-common-bin"
fi

echo "=== 6/6 status ==="
systemctl is-system-running || true
echo
ip -4 -br addr   || true
echo
echo "=== failed units ==="
systemctl --failed --no-legend --no-pager || true
echo
echo "done. if the board is on a router, look for the 192.168.101.x address above."
echo "the rescue address 10.9.8.77/24 is always present for a direct PC link."
