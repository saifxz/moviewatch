#!/bin/sh
set -e

# A leftover lock from a previous container stops Xvfb from starting.
rm -f /tmp/.X99-lock

# A killed container leaves Chrome's singleton lock behind, which blocks the
# next launch of the persistent BookMyShow profile.
rm -f /app/data/bms-browser/SingletonLock \
      /app/data/bms-browser/SingletonCookie \
      /app/data/bms-browser/SingletonSocket

# Headed Chrome needs a display even though the VM has no monitor.
Xvfb :99 -screen 0 1365x900x24 -nolisten tcp &

if [ "${ENABLE_VNC:-0}" = "1" ]; then
    x11vnc -display :99 -nopw -listen 0.0.0.0 -N &
fi

exec python -m movie_watch "$@"
