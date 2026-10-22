#!/usr/bin/env bash
# Publiceer data/site op de gh-pages-branch van origin (GitHub Pages).
# Eén commit zonder geschiedenis: oude data stapelt zich niet op in de repo.
set -euo pipefail
cd "$(dirname "$0")/.."
SITE=data/site
[ -f "$SITE/index.html" ] && [ -f "$SITE/data/stops.json" ] || { echo "Geen complete $SITE; draai eerst 'make site'." >&2; exit 1; }
REMOTE=$(git remote get-url origin)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
cp -R "$SITE"/. "$TMP"/
touch "$TMP/.nojekyll"
git -C "$TMP" init -q -b gh-pages
git -C "$TMP" add -A
git -C "$TMP" -c user.name="$(git config user.name)" -c user.email="$(git config user.email)" \
  commit -q -m "Webviewer: data $(date +%F) ($(git rev-parse --short HEAD))"
git -C "$TMP" push -q -f "$REMOTE" gh-pages
echo "Gepubliceerd naar gh-pages van $REMOTE"
