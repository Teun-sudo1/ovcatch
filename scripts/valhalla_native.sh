#!/usr/bin/env bash
# Valhalla zonder Docker, via de pyvalhalla-wheel (bevat valhalla_build_tiles en valhalla_service).
#   scripts/valhalla_native.sh build   # tiles bouwen uit data/raw/*.osm.pbf (eenmalig)
#   scripts/valhalla_native.sh serve   # server op http://localhost:8002 (voorgrond)
set -euo pipefail
cd "$(dirname "$0")/.."
BIN=${BIN:-$PWD/.venv/bin}
export PATH="$BIN:$PATH"  # de pyvalhalla-launchers zoeken de executables via PATH
DIR=data/valhalla
CONF=$DIR/valhalla.json

case "${1:-}" in
  build)
    mkdir -p "$DIR"
    shopt -s nullglob
    pbfs=(data/raw/*.osm.pbf)
    [ ${#pbfs[@]} -gt 0 ] || { echo "Geen data/raw/*.osm.pbf; draai eerst 'make download'." >&2; exit 1; }
    "$BIN/valhalla_build_config" \
      --mjolnir-tile-dir "$DIR/tiles" \
      --mjolnir-tile-extract "$DIR/tiles.tar" \
      --mjolnir-admin "$DIR/admins.sqlite" \
      --mjolnir-timezone "$DIR/timezones.sqlite" \
      --httpd-service-listen "tcp://*:8002" \
      --service-limits-isochrone-max-distance-contour 25 \
      > "$CONF"
    "$BIN/valhalla_build_admins" -c "$CONF" "${pbfs[@]}"
    "$BIN/valhalla_build_tiles" -c "$CONF" "${pbfs[@]}"
    "$BIN/valhalla_build_extract" -c "$CONF" -v --overwrite
    ;;
  serve)
    [ -f "$CONF" ] || { echo "Geen $CONF; draai eerst '$0 build'." >&2; exit 1; }
    exec "$BIN/valhalla_service" "$CONF" "${THREADS:-$(sysctl -n hw.ncpu 2>/dev/null || nproc)}"
    ;;
  *)
    echo "gebruik: $0 build|serve" >&2; exit 2 ;;
esac
