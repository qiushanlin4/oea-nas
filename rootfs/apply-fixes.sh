#!/bin/bash
# apply-fixes.sh -- apply the board-side patches to a freshly built rootfs image.
#
# Every patch in patches/ exists because something actually broke.  Read the
# comment above each one before removing it.
#
# Usage:
#   WORK=~/oea IMG=~/oea/newrootfs.img ./apply-fixes.sh
#
# Prerequisite: symlinks and metadata already restored (mkfs1g.sh + mkmeta.sh).

set -e
export PATH="/c/cygwin64/usr/bin:/c/cygwin64/usr/sbin:$PATH"

WORK="${WORK:-$PWD}"
IMG="${IMG:-$WORK/newrootfs.img}"
HERE=$(cd "$(dirname "$0")" && pwd)
P="$HERE/patches"

cd "$WORK"

if [ ! -f "$IMG" ]; then
    echo "missing image: $IMG" >&2
    exit 1
fi

# debugfs `write` does NOT accept an absolute destination path -- it treats the
# whole string as one filename and creates a file literally called
# "/etc/pam.d/login" in the current directory.  We learned this the expensive
# way: e2fsck then reports
#     Entry '/etc/pam.d/login' in / (2) has illegal characters in its name.
# The fix is to `cd` into the target directory first and use a bare filename.

put() {
    dir="$1"; name="$2"; src="$3"; mode="$4"
    [ -f "$src" ] || { echo "  SKIP $name (missing $src)"; return 0; }
    {
        echo "cd $dir"
        echo "rm $name"
        echo "write $src $name"
        echo "sif $name mode 0$mode"
        echo "sif $name uid 0"
        echo "sif $name gid 0"
    } > "$WORK/.apply.txt"
    debugfs -w -f "$WORK/.apply.txt" "$IMG" >/dev/null 2>&1 || true
    echo "  OK   $dir/$name  (mode $mode)"
}

echo "=== applying board patches to $IMG ==="

# ---------------------------------------------------------------------------
# 1. PAM  (/etc/pam.d/login)
#
#    The stock login stack ends with
#        session    optional   pam_lastlog.so
#    but the Armbian rootfs does not ship pam_lastlog.so.  PAM resolves every
#    module in the stack inside pam_start(), `optional` included, so ONE
#    missing file aborts the entire stack:
#        login: PAM Failure, aborting: Critical error - immediate abort
#    The board boots perfectly and nothing can log in -- serial or console.
#    Note `optional` does NOT make a missing module tolerable: optional only
#    means a module *failure* is ignored, not a failed dlopen.
#
#    patches/login.patched is the same file with that one line removed.
# ---------------------------------------------------------------------------
put /etc/pam.d login "$P/login.patched" 100644

# ---------------------------------------------------------------------------
# 2. /etc/securetty lists only ttyAML0.  The OEA console is ttyFIQ0.
#    Nothing references pam_securetty today, but the moment anything does,
#    root is refused on the only console this board has.
# ---------------------------------------------------------------------------
put /etc securetty "$P/securetty" 100644

# ---------------------------------------------------------------------------
# 3. Serial getty override: bypass login/PAM entirely.
#
#    ExecStart=-/bin/sh -c '/usr/local/sbin/netup.sh; exec /bin/bash --login'
#
#    This is what got us a shell while PAM was still broken, and it stays
#    useful afterwards: the console always gives you root, no password, no
#    PAM, no getty timing games over a 1500000-baud line.
# ---------------------------------------------------------------------------
put /etc/systemd/system/serial-getty@.service.d override.conf \
    "$P/serial-override.conf" 100644

# ---------------------------------------------------------------------------
# 4. netup.service + netup.sh
#
#    NOTHING else brings the wired NIC up on this setup:
#      - Ubuntu Server's 10-globally-managed-devices.conf excludes wired
#        devices from NetworkManager
#      - netplan has version/renderer but no ethernets section
#      - /etc/network/interfaces is empty
#    Result: no carrier, no DHCP, board unreachable.
# ---------------------------------------------------------------------------
put /etc/systemd/system netup.service "$P/netup.service" 100644
put /usr/local/sbin  netup.sh       "$P/netup.sh"       100755

# wants symlinks (systemctl enable, done at image level)
{
    echo "cd /etc/systemd/system/multi-user.target.wants"
    echo "symlink netup.service /etc/systemd/system/netup.service"
} > "$WORK/.apply.txt"
debugfs -w -f "$WORK/.apply.txt" "$IMG" >/dev/null 2>&1 || true
echo "  OK   multi-user.target.wants/netup.service"

# ---------------------------------------------------------------------------
# 5. Make sure the serial getty is actually wanted on ttyFIQ0.
#    (The Armbian image ships getty@tty1 and serial-getty@ttyAML0 only.)
# ---------------------------------------------------------------------------
{
    echo "cd /etc/systemd/system/getty.target.wants"
    echo "symlink serial-getty@ttyFIQ0.service /usr/lib/systemd/system/serial-getty@.service"
} > "$WORK/.apply.txt"
debugfs -w -f "$WORK/.apply.txt" "$IMG" >/dev/null 2>&1 || true
echo "  OK   getty.target.wants/serial-getty@ttyFIQ0.service"

echo
echo "=== verify ==="
printf "lastlog refs in pam.d/login : "
debugfs -R "cat /etc/pam.d/login" "$IMG" 2>/dev/null | grep -c lastlog || echo 0
printf "/bin is a symlink           : "
debugfs -R "stat /bin" "$IMG" 2>/dev/null | grep -c 'Type: symlink' || echo 0
printf "netup.sh mode               : "
debugfs -R "stat /usr/local/sbin/netup.sh" "$IMG" 2>/dev/null | grep -o 'Mode: *[0-9]*' || echo "?"
printf "securetty contents          : "
debugfs -R "cat /etc/securetty" "$IMG" 2>/dev/null | tr '\n' ' '; echo

echo
echo "NEXT:  e2fsck -fy $IMG"
