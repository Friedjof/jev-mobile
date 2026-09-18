# Production deployment

`compose.production.yml` deploys the released image without a source checkout or
local build. It creates two least-privilege roles:

- `jev-mobile-worker` owns USB, ADB keys and the provider secret;
- `jev-mobile-mcp` exposes Streamable HTTP only on the external OpenClaw
  network and has no USB, ADB keys, provider key, host port or Docker socket.

Both roles bind the same local `/data/jev-mobile.db`. Do not place this SQLite
WAL database on NFS, SMB or a Synology shared folder backed by a network mount.

## Install

Download the production artifacts from a release (a Git clone is not needed),
then pin the released image explicitly:

```bash
export JEV_MOBILE_VERSION=vX.Y.Z
gh release download "$JEV_MOBILE_VERSION" --repo Friedjof/jev-mobile \
  --pattern compose.production.yml --pattern jev-mobile-usb-hook
export JEV_MOBILE_IMAGE="ghcr.io/friedjof/jev-mobile-mcp:${JEV_MOBILE_VERSION#v}"
```

For releases predating the attached Compose artifact, download it from the
matching repository tag after verifying its checksum. Never deploy `latest` as
the rollback source.

Create role-specific configuration outside the application directory:

```bash
install -d -m 0700 "$HOME/.config/jev-mobile/secrets" "$HOME/.local/share/jev-mobile"
install -m 0600 /dev/null "$HOME/.config/jev-mobile/worker.env"
install -m 0600 /dev/null "$HOME/.config/jev-mobile/mcp.env"
install -m 0600 /dev/null "$HOME/.config/jev-mobile/secrets/typesafe_api_key"
printf 'MOBILE_DEVICE_SERIAL=%s\n' 'YOUR_ADB_SERIAL' > "$HOME/.config/jev-mobile/worker.env"
printf 'MOBILE_DEVICE_SERIAL=%s\n' 'YOUR_ADB_SERIAL' > "$HOME/.config/jev-mobile/mcp.env"
```

Write only the TypeSafe key to `typesafe_api_key`. Export host paths and the
numeric group that owns Android USB nodes:

```bash
export JEV_MOBILE_ENV_FILE="$HOME/.config/jev-mobile/worker.env"
export JEV_MOBILE_MCP_ENV_FILE="$HOME/.config/jev-mobile/mcp.env"
export JEV_MOBILE_TYPESAFE_API_KEY_FILE="$HOME/.config/jev-mobile/secrets/typesafe_api_key"
export JEV_MOBILE_DATA_DIR="$HOME/.local/share/jev-mobile"
export JEV_MOBILE_ADB_KEYS_DIR="$HOME/.android"
export JEV_MOBILE_UID="$(id -u)"
export JEV_MOBILE_GID="$(id -g)"
export JEV_MOBILE_USB_GID="$(getent group plugdev | cut -d: -f3)"
export JEV_MOBILE_OPENCLAW_NETWORK=openclaw-net
docker network inspect openclaw-net >/dev/null 2>&1 || docker network create openclaw-net
docker compose -f compose.production.yml pull
docker compose -f compose.production.yml up -d
```

The MCP URL inside that network is
`http://jev-mobile-mcp:8851/mcp`. Neither service publishes a host port. Stdio
remains supported separately through `jev-mobile-mcp --transport stdio`.

Check liveness and the richer deployment diagnostics:

```bash
docker inspect --format '{{.State.Health.Status}}' jev-mobile-worker
docker exec jev-mobile-worker jev-mobile doctor --backend portal-adb --json
docker exec jev-mobile-mcp python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8851/health').read().decode())"
```

`/health` is process/SQLite liveness. `/ready` additionally requires fresh
worker telemetry and a ready device/backend/provider.

## Upgrade and rollback

Take an online SQLite backup before changing images:

```bash
docker compose -f compose.production.yml run --rm --no-deps --entrypoint python jev-mobile-mcp -c \
  "import sqlite3; source=sqlite3.connect('/data/jev-mobile.db'); target=sqlite3.connect('/data/jev-mobile.pre-upgrade.db'); source.backup(target); target.close(); source.close()"
export PREVIOUS_JEV_MOBILE_IMAGE="$JEV_MOBILE_IMAGE"
export JEV_MOBILE_IMAGE=ghcr.io/friedjof/jev-mobile-mcp:NEW_VERSION
docker compose -f compose.production.yml pull
docker compose -f compose.production.yml up -d
```

Verify `doctor --json`, MCP `/health`, `/ready`, and one fixture task. To roll
back application containers, restore the previous pinned image and recreate
them:

```bash
export JEV_MOBILE_IMAGE="$PREVIOUS_JEV_MOBILE_IMAGE"
docker compose -f compose.production.yml up -d
```

Restore the database backup only if release notes explicitly require it. Stop
both services first and preserve the failed database for diagnosis.

## Synology USB reconnect hook

DSM may recreate a reconnected Android node as `root:root 0600`. The hook in
`deploy/synology/jev-mobile-usb-hook` matches one configured USB vendor/product
pair, changes only that node to the configured group and `0660`, restarts only
`jev-mobile-worker`, then verifies the configured serial with bounded ADB
retries inside that container. It never grants global USB permissions.

Determine the real IDs using `lsusb`, then inspect the planned installation:

```bash
sudo env JEV_USB_VENDOR_ID=22b8 JEV_USB_PRODUCT_ID=2e82 \
  JEV_USB_SERIAL=YOUR_ADB_SERIAL JEV_USB_GROUP=jev-usb \
  ./jev-mobile-usb-hook --dry-run
```

Install idempotently and verify:

```bash
sudo env JEV_USB_VENDOR_ID=22b8 JEV_USB_PRODUCT_ID=2e82 \
  JEV_USB_SERIAL=YOUR_ADB_SERIAL JEV_USB_GROUP=jev-usb \
  JEV_WORKER_CONTAINER=jev-mobile-worker \
  ./jev-mobile-usb-hook --install
sudo ./jev-mobile-usb-hook --verify
```

The installed root-owned config contains identifiers only, never credentials.
Rollback removes only the Jev Mobile rule, handler and config:

```bash
sudo ./jev-mobile-usb-hook --rollback
```

If a DSM update replaces its udev implementation, run `--verify` before relying
on automatic reconnect. Do not replace this targeted rule with `0666`, a broad
USB rule, privileged containers, or TCP ADB.
