#!/bin/sh
# Render's persistent disk mounts at /app/state -- on first boot it's empty
# and shadows whatever the image baked in at that path, so goal.yaml/
# strategy.yaml/etc. are invisible until seeded here. Once the disk has
# real state (e.g. reflect.py has since bumped strategy.yaml), this must
# never overwrite it -- only seed when goal.yaml is genuinely absent.
set -e

mkdir -p /app/state
if [ ! -f /app/state/goal.yaml ]; then
    echo "state/goal.yaml not found -- seeding /app/state from image defaults (first boot on this disk)"
    cp -rn /app/state_defaults/. /app/state/
fi

exec "$@"
