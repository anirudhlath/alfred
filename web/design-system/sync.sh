#!/usr/bin/env bash
# Regenerates the Alfred design system (web/design-system/out/) from the web
# client as it is now. See docs/design-system.md. Any step that disagrees with
# the code stops the run: a story whose props no longer type-check, a stylesheet
# that changed shape, a card that throws or renders nothing.
set -euo pipefail
cd "$(dirname "$0")"
TSC=../node_modules/.bin/tsc

echo "1/6 stylesheet from web/src/index.css";  node gen-css.mjs
echo "2/6 stories type-check against web/src"; "$TSC" -p tsconfig.previews.json
echo "3/6 declarations from web/src";          rm -rf .cache/decl && "$TSC" -p tsconfig.decl.json
echo "4/6 bundle, vendor, previews";           rm -rf out && node build.mjs
echo "5/6 docs, types, cards";                 node gen-docs.mjs
if [ "${DESIGN_SYNC_SKIP_RENDER:-}" = "1" ]; then
  echo "6/6 render check SKIPPED (DESIGN_SYNC_SKIP_RENDER=1): do not upload this build"
else
  echo "6/6 render check in Chromium";         node render-check.mjs
fi
