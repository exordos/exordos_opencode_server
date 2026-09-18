#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
# Licensed under the Apache License, Version 2.0 (the "License").

set -euo pipefail

# shellcheck disable=SC1091
source /usr/local/lib/exordos/lib_bootstrap.sh
# The Core helper enables xtrace. Disable it before a secret reaches this script.
set +x

BOOTSTRAP_COMPLETE="/var/lib/opencode_server/opencode-server-bootstrap-v1-complete"
SERVER_ENV="/etc/opencode_server/server.env"
SERVICE_USER="opencode"
SERVICE_GROUP="opencode"
STATE_DIR="/var/lib/opencode_server"

persistent_disk=$(find_persistent_disk)
if [[ -z "$persistent_disk" ]]; then
    echo "OpenCode Server requires a persistent data disk" >&2
    exit 1
fi

prepare_persistent_disk "$persistent_disk" "$PERSISTENT_MOUNT"
if ! mountpoint --quiet "$STATE_DIR"; then
    migrate_to_persistent \
        "$STATE_DIR" \
        "${PERSISTENT_MOUNT}${STATE_DIR}" \
        "$SERVICE_USER" \
        "$SERVICE_GROUP"
    persist_migrate_complete
fi

sudo install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 \
    "$STATE_DIR/cache" \
    "$STATE_DIR/config" \
    "$STATE_DIR/data" \
    "$STATE_DIR/state" \
    "$STATE_DIR/workspace"

for _ in $(seq 1 150); do
    if [[ -s "$SERVER_ENV" ]] \
        && grep -qx 'OPENCODE_SERVER_USERNAME=opencode' "$SERVER_ENV" \
        && grep -q '^OPENCODE_SERVER_PASSWORD=.' "$SERVER_ENV"; then
        break
    fi
    sleep 2
done

grep -qx 'OPENCODE_SERVER_USERNAME=opencode' "$SERVER_ENV"
grep -q '^OPENCODE_SERVER_PASSWORD=.' "$SERVER_ENV"
sudo chown root:"$SERVICE_GROUP" "$SERVER_ENV"
sudo chmod 0640 "$SERVER_ENV"

sudo systemctl enable --now opencode-server.service

for _ in $(seq 1 60); do
    if sudo /usr/local/bin/opencode-server-health; then
        sudo install -o root -g root -m 0644 /dev/null "$BOOTSTRAP_COMPLETE"
        exit 0
    fi
    sleep 2
done

echo "OpenCode Server did not become healthy" >&2
exit 1
