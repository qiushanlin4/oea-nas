#!/bin/sh
# OEA NAS - pin every CPU policy to the "performance" governor at boot.
#
# IMPORTANT: this is NOT overclocking. "performance" only selects the
# highest OPP that the kernel has already registered (1104000 kHz /
# ~1275 mV on this RK3318). It never touches the device tree, the PLL
# bypass, or regulator limits, so a reboot always returns to a safe,
# vendor-sanctioned state even if this script fails.
#
# Companion: cpu-performance.service  (see docs/11-performance.md)

set -u
GOV=performance
ROOT=/sys/devices/system/cpu

log() { printf 'cpu-performance: %s\n' "$*" > /dev/kmsg 2>/dev/null || true; }

# Wait (bounded) until cpufreq-dt has exposed its sysfs nodes. This unit
# runs early in multi-user.target; the driver may still be probing.
i=0
while [ ! -f "$ROOT/cpu0/cpufreq/scaling_governor" ]; do
    i=$((i + 1))
    if [ "$i" -ge 30 ]; then
        log "cpufreq sysfs never appeared; giving up (system stays at kernel default)"
        exit 1
    fi
    sleep 1
done

apply() {
    for g in "$ROOT"/cpu[0-9]*/cpufreq/scaling_governor; do
        [ -w "$g" ] || continue
        printf '%s\n' "$GOV" > "$g" 2>/dev/null || log "write failed: $g"
    done
}

# Apply twice: a late CPU hotplug / driver re-init can reset one policy.
apply
sleep 2
apply

bad=0
for g in "$ROOT"/cpu[0-9]*/cpufreq/scaling_governor; do
    v=$(cat "$g" 2>/dev/null || echo "?")
    [ "$v" = "$GOV" ] || { bad=1; log "policy not locked: $g = $v"; }
done
if [ "$bad" -eq 0 ]; then
    log "all CPU policies locked to performance (max registered OPP)"
else
    exit 1
fi
