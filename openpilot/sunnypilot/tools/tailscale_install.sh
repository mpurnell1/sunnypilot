#!/usr/bin/env bash
# Tailscale on the device for the nav transport's tailnet route (the forum guide's
# "Advanced: Tailscale"). The static build lands in /data/tailscale and the process
# manager runs it from there (system/manager/process_config.py), so it survives
# sunnypilot and AGNOS updates; nothing is written outside /data and nothing needs root.
# Rerun with a newer TAILSCALE_VERSION to upgrade, then reboot for the new daemon.
set -euo pipefail

VERSION=${TAILSCALE_VERSION:-1.102.4}  # https://pkgs.tailscale.com/stable/#static
DIR=/data/tailscale
SOCK=$DIR/tailscaled.sock

if [ -f /etc/systemd/system/tailscaled.service ]; then
  echo "tailscaled is already installed as a systemd unit on this device; nothing to do" >&2
  exit 1
fi

name=tailscale_${VERSION}_arm64
# /tmp is a 150 MB tmpfs; the unpacked binaries are 70 MB
tmp=$(mktemp -d /data/tailscale-dl.XXXXXX)
trap 'rm -rf "$tmp"' EXIT
echo "downloading $name.tgz"
curl -fL --progress-bar "https://pkgs.tailscale.com/stable/$name.tgz" \
  | tar -xz -C "$tmp" --strip-components=1 "$name/tailscale" "$name/tailscaled"
mkdir -p "$DIR"
mv -f "$tmp/tailscale" "$tmp/tailscaled" "$DIR/"

echo "waiting for the process manager to start tailscaled"
for _ in $(seq 30); do
  [ -S "$SOCK" ] && break
  sleep 1
done
if [ ! -S "$SOCK" ]; then
  echo "tailscaled did not come up; is sunnypilot running? (sudo systemctl start comma)" >&2
  exit 1
fi

"$DIR/tailscale" --socket="$SOCK" up
echo "this device's tailnet address: $("$DIR/tailscale" --socket="$SOCK" ip -4)"
