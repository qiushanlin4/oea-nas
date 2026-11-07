#!/bin/bash
# serial.sh -- minimal serial helper for Linux/macOS.
#
# The Windows equivalents are ../windows/serial/ser.ps1 and keys.ps1.
# This one exists because when the board dies you may be on any machine.
#
# Usage:
#   ./serial.sh <command> [seconds]
#   ./serial.sh ''        5          # just listen for 5 s
#   ./serial.sh 'fiq'                # enter the FIQ debugger
#   ./serial.sh 'reset bootloader' 15
#
# Environment:
#   SERIAL_PORT   default /dev/ttyUSB0   (CH340 shows up as ttyUSB0)
#   SERIAL_BAUD   default 1500000
#
# NOTE ON BAUD: 1500000 is not a standard rate.  Some cheap USB-UART bridges
# silently fall back to 115200.  If you get garbage, try 115200 -- the board
# does print a firstrun prompt at that rate on some builds.

set -u

PORT="${SERIAL_PORT:-/dev/ttyUSB0}"
BAUD="${SERIAL_BAUD:-1500000}"
CMD="${1:-}"
SECS="${2:-8}"

if [ ! -e "$PORT" ]; then
    echo "no such serial port: $PORT" >&2
    echo "available:" >&2
    ls -1 /dev/ttyUSB* /dev/ttyACM* 2>/dev/null >&2 || echo "  (none)" >&2
    exit 1
fi

# raw 8N1, no flow control, no echo
stty -F "$PORT" "$BAUD" raw -echo -echoe -echok -crtscts 2>/dev/null || {
    echo "cannot set $BAUD on $PORT -- the adapter may not support it" >&2
    exit 1
}

# drain anything already buffered
timeout 0.2 cat "$PORT" >/dev/null 2>&1 || true

# start the reader first so we never miss the reply
timeout "$SECS" cat "$PORT" &
READER=$!

sleep 1
if [ -n "$CMD" ]; then
    printf '%s\r' "$CMD" > "$PORT"
    echo ">>> $CMD" >&2
fi

wait $READER 2>/dev/null
echo >&2
