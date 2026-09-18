#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
# Licensed under the Apache License, Version 2.0 (the "License").

set -euo pipefail

APP_DIR="/opt/exordos_opencode_server"
BOOTSTRAP_DIR="/var/lib/exordos/bootstrap/scripts"
CONFIG_DIR="/etc/opencode_server"
SERVICE_USER="opencode"
SERVICE_GROUP="opencode"
STATE_DIR="/var/lib/opencode_server"
SYSTEMD_DIR="/etc/systemd/system"
OPENCODE_VERSION="1.18.31"
OPENCODE_SHA256="b283e8dbe9e6fc224bb4b79992ce3bd2174b8b7b0c3e7d1b4e6024a1d11edc84"
OPENCODE_ASSET="opencode-linux-x64-baseline.tar.gz"
OPENCODE_URL="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${OPENCODE_ASSET}"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "The OpenCode Server image currently supports x86_64 only" >&2
    exit 1
fi

sudo apt-get -o DPkg::Lock::Timeout=300 update
sudo DEBIAN_FRONTEND=noninteractive apt-get \
    -o DPkg::Lock::Timeout=300 install -y \
    ca-certificates \
    curl

if ! getent group "$SERVICE_GROUP" >/dev/null; then
    sudo groupadd --system "$SERVICE_GROUP"
fi

if ! getent passwd "$SERVICE_USER" >/dev/null; then
    sudo useradd \
        --system \
        --gid "$SERVICE_GROUP" \
        --home-dir "$STATE_DIR" \
        --create-home \
        --shell /usr/sbin/nologin \
        "$SERVICE_USER"
fi

temporary_dir=$(mktemp -d /tmp/opencode-server.XXXXXX)
trap 'rm -rf -- "$temporary_dir"' EXIT
curl \
    --fail \
    --location \
    --retry 3 \
    --connect-timeout 10 \
    --max-time 300 \
    "$OPENCODE_URL" \
    --output "$temporary_dir/$OPENCODE_ASSET"
printf '%s  %s\n' "$OPENCODE_SHA256" "$temporary_dir/$OPENCODE_ASSET" \
    | sha256sum --check --strict
tar -xzf "$temporary_dir/$OPENCODE_ASSET" -C "$temporary_dir"
[[ "$("$temporary_dir/opencode" --version)" == "$OPENCODE_VERSION" ]]
sudo install -o root -g root -m 0755 \
    "$temporary_dir/opencode" /usr/local/bin/opencode

sudo install -d -o root -g "$SERVICE_GROUP" -m 0750 "$CONFIG_DIR"
sudo install -d -o "$SERVICE_USER" -g "$SERVICE_GROUP" -m 0700 \
    "$STATE_DIR" \
    "$STATE_DIR/cache" \
    "$STATE_DIR/config" \
    "$STATE_DIR/data" \
    "$STATE_DIR/state" \
    "$STATE_DIR/workspace"
sudo install -d -o root -g root -m 0755 "$BOOTSTRAP_DIR"

sudo install -o root -g root -m 0644 \
    "$APP_DIR/etc/opencode.jsonc" \
    "$CONFIG_DIR/opencode.jsonc"
sudo install -o root -g root -m 0644 \
    "$APP_DIR/etc/systemd/opencode-server.service" \
    "$SYSTEMD_DIR/opencode-server.service"
sudo install -o root -g root -m 0755 \
    "$APP_DIR/scripts/opencode-server-health" \
    /usr/local/bin/opencode-server-health
sudo install -o root -g root -m 0755 \
    "$APP_DIR/scripts/opencode-server-reload" \
    /usr/local/bin/opencode-server-reload
sudo install -o root -g root -m 0755 \
    "$APP_DIR/scripts/opencode-server-validate" \
    /usr/local/bin/opencode-server-validate
sudo install -o root -g root -m 0755 \
    "$APP_DIR/exordos/images/bootstrap.sh" \
    "$BOOTSTRAP_DIR/0100-opencode-server.sh"

sudo --user "$SERVICE_USER" env \
    HOME="$STATE_DIR" \
    XDG_CONFIG_HOME="$STATE_DIR/config" \
    XDG_DATA_HOME="$STATE_DIR/data" \
    XDG_CACHE_HOME="$STATE_DIR/cache" \
    XDG_STATE_HOME="$STATE_DIR/state" \
    OPENCODE_CONFIG="$CONFIG_DIR/opencode.jsonc" \
    /bin/bash -c \
        'cd /var/lib/opencode_server/workspace && /usr/local/bin/opencode debug config >/dev/null'

sudo systemctl disable --now opencode-server.service || true
sudo systemctl daemon-reload
