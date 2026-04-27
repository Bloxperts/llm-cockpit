#!/usr/bin/env bash
# scripts/build-frontend.sh
#
# Build the Next.js static export and copy it into src/cockpit/frontend_dist/
# so the next `python -m build` ships an installable wheel with the frontend
# baked in. Slice B / Slice C of UC-04+UC-05 (Sprint 4) — runbook-aligned.
#
# Usage:
#   bash scripts/build-frontend.sh       # build + copy
#
# Exits non-zero if `node` / `npm` aren't on PATH or if the Next build fails.
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FRONTEND_DIR="$ROOT/frontend"
DIST_DIR="$ROOT/src/cockpit/frontend_dist"

if ! command -v node >/dev/null 2>&1; then
  echo "ERROR: node is required to build the frontend. Install Node 20+ and retry." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "ERROR: npm is required to build the frontend. Install Node 20+ and retry." >&2
  exit 1
fi
if [ ! -d "$FRONTEND_DIR" ]; then
  echo "ERROR: $FRONTEND_DIR does not exist. Are you in the cockpit repo root?" >&2
  exit 1
fi

cd "$FRONTEND_DIR"

if [ ! -d node_modules ]; then
  echo "[build-frontend] running npm install (no lockfile sync) ..."
  npm install --no-audit --no-fund
fi

echo "[build-frontend] running next build (static export) ..."
npm run build

if [ ! -d "$FRONTEND_DIR/out" ]; then
  echo "ERROR: next build did not produce the expected $FRONTEND_DIR/out directory." >&2
  exit 1
fi

echo "[build-frontend] replacing $DIST_DIR with the fresh static export ..."
rm -rf "$DIST_DIR"
mkdir -p "$DIST_DIR"
cp -R "$FRONTEND_DIR/out/." "$DIST_DIR/"

echo "[build-frontend] done. Frontend assets in $DIST_DIR"
