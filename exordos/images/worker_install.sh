#!/usr/bin/env bash

# Copyright 2026 Genesis Corporation
# Licensed under the Apache License, Version 2.0 (the "License").

# Builds the disposable OpenCode build worker image.
#
# A worker is one VM handed out to exactly one lease and destroyed after
# it. The client logs in over SSH as `ubuntu` (its key is delivered by
# Exordos Core when the lease is bound) and runs the OpenCode CLI, the
# Exordos CLI and image builds (packer + qemu/KVM) there, with
# gcc/g++, a JDK and Go for building elements in those languages. A build
# of code the client does not trust - image builds included - runs as
# `builder`, which cannot read `ubuntu`'s files (the pool's opencode.json,
# with the model provider's key). Nothing runs as a service: the image is
# ready as soon as the VM boots.

set -euo pipefail

APP_DIR="/opt/exordos_opencode_server"
WORKER_USER="ubuntu"
WORKER_HOME="/home/${WORKER_USER}"
BUILDER_USER="builder"
BUILDER_HOME="/home/${BUILDER_USER}"

OPENCODE_VERSION="1.19.3"
OPENCODE_SHA256="531cff2441129c30f0edd5878af494108ad07f5feb46d7ee29a2a5b830a5765d"
OPENCODE_ASSET="opencode-linux-x64-baseline.tar.gz"
OPENCODE_URL="https://github.com/anomalyco/opencode/releases/download/v${OPENCODE_VERSION}/${OPENCODE_ASSET}"

EXORDOS_VERSION="3.2.30"
EXORDOS_SHA256="ba821dc2d70d140de61bd3d5bb487b8b755876b3aebb93a55dcdd00b9db6c4c0"
EXORDOS_URL="https://github.com/exordos/exordos/releases/download/${EXORDOS_VERSION}/exordos-linux"

PACKER_VERSION="1.9.2"
PACKER_SHA256="34fe48d0d5f99670af15d8a3b581db7ce9d08093ce37240d7c7b996de7947275"
PACKER_ASSET="packer_${PACKER_VERSION}_linux_amd64.zip"
PACKER_URL="https://hashicorp-releases.yandexcloud.net/packer/${PACKER_VERSION}/${PACKER_ASSET}"

# Packer plugins required by the exordos_base build profile. `packer plugins
# install` verifies the release checksum, so builds on a worker do not
# download them at the first `exordos build`.
PACKER_SSHKEY_PLUGIN_VERSION="1.4.0"
PACKER_QEMU_PLUGIN_VERSION="1.1.3"

GO_VERSION="1.27.2"
GO_SHA256="ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5"
GO_ASSET="go${GO_VERSION}.linux-amd64.tar.gz"
GO_URL="https://go.dev/dl/${GO_ASSET}"

UV_VERSION="0.12.24"
UV_SHA256="b4dfaef47d491a7296981f8374a4595f55dbf84e8937c8ecd2983574d8bb3da6"
UV_ASSET="uv-x86_64-unknown-linux-gnu.tar.gz"
UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_ASSET}"

if [[ "$(uname -m)" != "x86_64" ]]; then
    echo "The OpenCode worker image currently supports x86_64 only" >&2
    exit 1
fi

download() {
    local url="$1" sha256="$2" output="$3"

    curl \
        --fail \
        --location \
        --retry 3 \
        --connect-timeout 10 \
        --max-time 600 \
        "$url" \
        --output "$output"
    printf '%s  %s\n' "$sha256" "$output" | sha256sum --check --strict
}

sudo apt-get -o DPkg::Lock::Timeout=300 update
sudo DEBIAN_FRONTEND=noninteractive apt-get \
    -o DPkg::Lock::Timeout=300 install -y \
    build-essential \
    ca-certificates \
    curl \
    genisoimage \
    git \
    default-jdk-headless \
    jq \
    openssh-server \
    qemu-system-x86 \
    qemu-utils \
    rsync \
    unzip \
    xz-utils \
    zstd

temporary_dir=$(mktemp -d /tmp/opencode-worker.XXXXXX)
trap 'rm -rf -- "$temporary_dir"' EXIT

# OpenCode: one pinned upstream release, verified, never auto-updated.
download "$OPENCODE_URL" "$OPENCODE_SHA256" "$temporary_dir/$OPENCODE_ASSET"
tar -xzf "$temporary_dir/$OPENCODE_ASSET" -C "$temporary_dir"
[[ "$("$temporary_dir/opencode" --version)" == "$OPENCODE_VERSION" ]]
sudo install -o root -g root -m 0755 \
    "$temporary_dir/opencode" /usr/local/bin/opencode

# Exordos CLI: builds and publishes elements.
download "$EXORDOS_URL" "$EXORDOS_SHA256" "$temporary_dir/exordos"
sudo install -o root -g root -m 0755 \
    "$temporary_dir/exordos" /usr/local/bin/exordos

# Packer: what `exordos build` drives.
download "$PACKER_URL" "$PACKER_SHA256" "$temporary_dir/$PACKER_ASSET"
unzip -q "$temporary_dir/$PACKER_ASSET" -d "$temporary_dir/packer"
sudo install -o root -g root -m 0755 \
    "$temporary_dir/packer/packer" /usr/local/bin/packer

# Go: pinned upstream toolchain, since distro packages lag behind.
download "$GO_URL" "$GO_SHA256" "$temporary_dir/$GO_ASSET"
sudo rm -rf /usr/local/go
sudo tar -xzf "$temporary_dir/$GO_ASSET" -C /usr/local
sudo ln -sf /usr/local/go/bin/go /usr/local/bin/go
sudo ln -sf /usr/local/go/bin/gofmt /usr/local/bin/gofmt

# uv: builds of Python projects take the interpreter they pin from it, and
# without root `builder` has no other way to.
download "$UV_URL" "$UV_SHA256" "$temporary_dir/$UV_ASSET"
tar -xzf "$temporary_dir/$UV_ASSET" -C "$temporary_dir"
sudo install -o root -g root -m 0755 \
    "$temporary_dir/uv-x86_64-unknown-linux-gnu/uv" \
    "$temporary_dir/uv-x86_64-unknown-linux-gnu/uvx" \
    /usr/local/bin/

# System managed OpenCode config: the highest precedence on Linux, so the
# pool's configuration can't turn automatic updates or sharing back on.
sudo install -d -o root -g root -m 0755 /etc/opencode
sudo install -o root -g root -m 0644 \
    "$APP_DIR/etc/opencode.jsonc" \
    /etc/opencode/opencode.jsonc

# The worker user. It owns the VM for the lease's lifetime: image builds
# need KVM, and the client may install whatever its build requires. The
# VM is single-tenant and destroyed with the lease, never reused.
if ! getent passwd "$WORKER_USER" >/dev/null; then
    sudo useradd \
        --create-home \
        --home-dir "$WORKER_HOME" \
        --shell /bin/bash \
        --groups kvm \
        "$WORKER_USER"
fi
sudo usermod --append --groups kvm "$WORKER_USER"
sudo usermod --lock "$WORKER_USER"
printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$WORKER_USER" \
    | sudo tee "/etc/sudoers.d/90-${WORKER_USER}" >/dev/null
sudo chmod 0440 "/etc/sudoers.d/90-${WORKER_USER}"
sudo visudo --check --file "/etc/sudoers.d/90-${WORKER_USER}"

# The control plane writes the pool's opencode.json here and Core's agent
# appends the lease key to authorized_keys.
sudo install -d -o "$WORKER_USER" -g "$WORKER_USER" -m 0700 \
    "$WORKER_HOME/.ssh" \
    "$WORKER_HOME/.config" \
    "$WORKER_HOME/.config/opencode"
# Whatever the base image's default: nobody else lists or opens anything
# under it.
sudo chmod 0700 "$WORKER_HOME"

# The build user, for code the client does not trust: a whole `exordos
# build`, whose artifact scripts run on the host (a static site's `npm ci`
# with its packages' install scripts) and whose images take KVM. No sudo,
# no password, no group of `ubuntu`'s; KVM lets it run VMs, not read
# another user's files. `ubuntu` runs it with `sudo -u`, hands it a
# directory to build in and takes the directory back after.
if ! getent passwd "$BUILDER_USER" >/dev/null; then
    sudo useradd \
        --create-home \
        --home-dir "$BUILDER_HOME" \
        --shell /bin/bash \
        --user-group \
        --groups kvm \
        "$BUILDER_USER"
fi
sudo usermod --append --groups kvm "$BUILDER_USER"
sudo usermod --lock "$BUILDER_USER"
sudo chmod 0700 "$BUILDER_HOME"

# /tmp on the disk. systemd mounts it as a tmpfs of half the RAM with a
# per-user quota, and image builds put their disks there (packer's
# output directory, `exordos build`'s temporary directories): a quota
# that runs out on the host shows inside the build VM as ENOSPC, on a
# disk that is nowhere near full.
sudo systemctl mask tmp.mount

# Key-only SSH.
sudo install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d
printf '%s\n' \
    "PasswordAuthentication no" \
    "KbdInteractiveAuthentication no" \
    "PermitRootLogin no" \
    | sudo tee /etc/ssh/sshd_config.d/60-opencode-worker.conf >/dev/null
sudo install -d -o root -g root -m 0755 /run/sshd
sudo sshd -t

# Packer plugins, preinstalled for both users that build images (packer
# keeps them in ~/.config/packer/plugins).
for user in "$WORKER_USER" "$BUILDER_USER"; do
    sudo -u "$user" -H packer plugins install \
        github.com/ivoronin/sshkey "v${PACKER_SSHKEY_PLUGIN_VERSION}"
    sudo -u "$user" -H packer plugins install \
        github.com/hashicorp/qemu "v${PACKER_QEMU_PLUGIN_VERSION}"
done

# Fail the build early on a broken toolchain.
opencode --version
exordos version
packer version
for user in "$WORKER_USER" "$BUILDER_USER"; do
    sudo -u "$user" -H packer plugins installed \
        | grep -F "packer-plugin-sshkey_v${PACKER_SSHKEY_PLUGIN_VERSION}_"
    sudo -u "$user" -H packer plugins installed \
        | grep -F "packer-plugin-qemu_v${PACKER_QEMU_PLUGIN_VERSION}_"
done
qemu-img --version
gcc --version
g++ --version
make --version
java -version
go version
uv --version
# The build user is no more than that. (`if`, not `!`: set -e ignores a
# negated command.)
if sudo -l -n -U "$BUILDER_USER" | grep -q "may run"; then
    echo "$BUILDER_USER may run sudo" >&2
    exit 1
fi
if id -nG "$BUILDER_USER" | grep -qw -e sudo -e "$WORKER_USER"; then
    echo "$BUILDER_USER is in a privileged group: $(id -nG "$BUILDER_USER")" >&2
    exit 1
fi
if sudo -u "$BUILDER_USER" test -r "$WORKER_HOME/.config/opencode"; then
    echo "$BUILDER_USER can read $WORKER_HOME/.config/opencode" >&2
    exit 1
fi
