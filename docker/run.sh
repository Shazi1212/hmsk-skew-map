#!/bin/sh
# Serve the static dashboard and rebuild the board once per weekday.
# Untested: Docker was not available where this was written.
set -u
cd /app

python -m http.server "${PORT:-8080}" --bind 0.0.0.0 --directory /srv/site &

build() {
  python -m skewmap.fetch --config config.yaml \
    || echo "build failed, keeping the previous board" >&2
}

# One build at start so the page has data, then daily at RUN_AT_UTC (HH:MM).
build
AT="${RUN_AT_UTC:-21:20}"
while true; do
  now=$(date -u +%s)
  next=$(date -u -d "today $AT" +%s)
  [ "$next" -le "$now" ] && next=$(date -u -d "tomorrow $AT" +%s)
  sleep $((next - now))
  [ "$(date -u +%u)" -le 5 ] && build
done
