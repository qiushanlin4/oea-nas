#!/bin/sh
# /usr/local/sbin/netup.sh  --  bring up the single wired NIC (v8).
#
# Why this exists:
#   The factory initramfs boots us into an overlay root.  Ubuntu Server's
#   NetworkManager is configured (10-globally-managed-devices.conf) to ignore
#   *wired* devices, netplan has no ethernets section, and /etc/network/interfaces
#   is empty.  Result: nothing ever brings eth0 up and the box is unreachable.
#
# Design (v8):
#   1. pick exactly one physical NIC
#   2. wait for CARRIER (a router port needs a few seconds to negotiate)
#   3. ALWAYS install the direct-PC static address 10.9.8.77/24 as a SECONDARY
#      address -> the "laptop <-> board" rescue path keeps working forever
#   4. run dhcpcd as a RESIDENT daemon -> plugging the cable into a router later
#      is picked up automatically, no reboot needed
#   5. keep a usable /etc/resolv.conf (a plain file; systemd-resolved is off)

STATIC_IP="10.9.8.77"
STATIC_PREFIX="24"
IFACE=""

# --- pick exactly one wired NIC -------------------------------------------
for d in /sys/class/net/*; do
    n=${d##*/}
    [ "$n" = "lo" ] && continue
    [ -e "$d/device" ] || continue
    case "$n" in
        wl*|wlan*|tun*|sit*|ip6*|ip_vti*|docker*|veth*|br-*|virbr*) continue ;;
    esac
    IFACE="$n"
done

[ -z "$IFACE" ] && exit 0

/bin/ip link set "$IFACE" up 2>/dev/null

# --- wait for carrier (max 25s) -------------------------------------------
i=0
while [ $i -lt 25 ]; do
    [ "$(cat /sys/class/net/$IFACE/carrier 2>/dev/null)" = "1" ] && break
    sleep 1
    i=$((i + 1))
done

# --- always install the rescue address first ------------------------------
/bin/ip addr add "${STATIC_IP}/${STATIC_PREFIX}" dev "$IFACE" 2>/dev/null

# --- DHCP daemon (survives cable moves) -----------------------------------
if command -v dhcpcd >/dev/null 2>&1; then
    if ! pgrep -f "dhcpcd.*${IFACE}" >/dev/null 2>&1; then
        dhcpcd --nohook resolv.conf,hostname,ntpconf --timeout 20 "$IFACE" \
            >/dev/null 2>&1
    fi
fi

# --- keep DNS working (resolv.conf is a plain file; systemd-resolved is off)
if [ ! -s /etc/resolv.conf ] || ! grep -q "^nameserver" /etc/resolv.conf 2>/dev/null; then
    cat > /etc/resolv.conf <<'DNSEOF'
nameserver 192.168.101.1
nameserver 223.5.5.5
nameserver 119.29.29.29
options edns0 trust-ad timeout:2 attempts:2
DNSEOF
    chmod 644 /etc/resolv.conf
fi

sleep 4
/bin/ip -br addr > /dev/console 2>&1 || true
exit 0
