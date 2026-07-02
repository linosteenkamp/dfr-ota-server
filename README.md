# dfr-ota-server

Pull-based Zigbee OTA aggregator for a private **DFR-\*** device family. A systemd
timer fetches each configured firmware repo's latest GitHub Release `.ota` +
`ota/index.json` (with a read-only token), verifies each image's `sha512`, rewrites the
URLs to a local address, merges everything into **one** Zigbee2MQTT override index, and
atomically publishes it. A tiny static server serves that index on `127.0.0.1:8091`,
where z2m (running on the same Pi) reads it. Rollout is **manual** — you click *Update*
per device in the z2m UI.

Source firmware repos stay private; nothing is exposed on the LAN.

- Python 3 **stdlib only** (no runtime `pip` dependencies).
- Runs as an unprivileged `dfr-ota` system user.
- Serves **loopback only** (z2m is co-located).

---

## How it works

```
Each DFR-* firmware repo (private):            This Pi:
  git tag vX.Y.Z  ──(GitHub Actions)──▶          dfr-ota-sync.timer  (every ~15 min)
    build → firmware-X.Y.Z.ota                     └▶ dfr-ota-sync.service (oneshot)
    → GitHub Release asset                            │ for each repo in repos.conf:
    → commit ota/index.json                           │   GitHub API: latest release + ota/index.json
                                                       │   download .ota asset (read-only token)
                                                       │   verify sha512 + size
                                                       │   rewrite url → http://127.0.0.1:8091/ota/<file>
                                                       │   merge into one index (namespaced filenames)
                                                       │ atomic swap → /var/lib/dfr-ota/pub
                                                       ▼
                                             dfr-ota-serve.service
                                             python3 -m http.server 8091 (127.0.0.1, read-only)
                                                       │
                                                       ▼  http://127.0.0.1:8091/index.json
                                             Zigbee2MQTT (override index; manual rollout)
```

Served `.ota` filenames are namespaced by `(manufacturerCode, imageType)` —
e.g. `fefe-0002-firmware-1.0.0.ota` — so two devices releasing the same version
(both `firmware-1.0.0.ota`) never collide.

---

## Software requirements (the Pi)

- Raspberry Pi OS / Debian (this is deployed on a Pi, `aarch64`).
- **`python3`** ≥ 3.9 (deployed on 3.11). Stdlib only — no `pip` packages for runtime.
- `systemd`, `git` or `scp`/`rsync` (to get this repo onto the Pi), `curl` (for checks).
- Outbound HTTPS to `api.github.com` (for the pull).
- A **fine-grained GitHub PAT**, **Contents: Read-only**, scoped to the `DFR-*` repos
  listed in `repos.conf`.

Nothing here builds firmware — that happens in each firmware repo's CI.

---

## Install / rebuild-the-Pi runbook

The full copy-paste path to get OTA working again on a fresh Pi.

### 1. Get this repo onto the Pi

The Pi typically has no GitHub credentials, so copy from a machine that has the repo:

```bash
# from your dev machine (where dfr-ota-server is checked out)
rsync -av --exclude .git ./dfr-ota-server/ lino@<pi>:~/dfr-ota-server/
# or: scp -r dfr-ota-server lino@<pi>:~/
```
(If the Pi *does* have git creds: `git clone https://github.com/linosteenkamp/dfr-ota-server ~/dfr-ota-server`.)

### 2. Run the installer (as root)

```bash
cd ~/dfr-ota-server
sudo ./install.sh
```
`install.sh` is idempotent. It creates the `dfr-ota` user, the state dirs, the config
files, installs the three systemd units, and enables the static server + timer. See
[What install.sh creates](#what-installsh-creates).

### 3. Add the read-only GitHub token

Mint a **fine-grained PAT** (GitHub → Settings → Developer settings → Fine-grained
tokens): *Repository access* = only your `DFR-*` firmware repos; *Permissions →
Repository → Contents: Read-only*. Then:

```bash
printf '%s' 'github_pat_XXXXXXXX' | sudo tee /etc/dfr-ota/token >/dev/null
sudo chgrp dfr-ota /etc/dfr-ota/token && sudo chmod 640 /etc/dfr-ota/token
```

### 4. List the firmware repos to track

```bash
sudo tee /etc/dfr-ota/repos.conf >/dev/null <<'EOF'
linosteenkamp/DFR-DoorSensor
linosteenkamp/DFR-MoistureTracker
EOF
sudo chgrp dfr-ota /etc/dfr-ota/repos.conf && sudo chmod 640 /etc/dfr-ota/repos.conf
```
One `owner/repo` per line; `#` comments and blank lines ignored. A repo with no release
yet is skipped, not an error.

### 5. Run the first sync and verify

```bash
sudo systemctl start dfr-ota-sync.service
journalctl -u dfr-ota-sync.service -n 20 --no-pager        # expect "served N image(s); failed repos: none"
curl -s http://127.0.0.1:8091/index.json | python3 -m json.tool
curl -sI http://127.0.0.1:8091/ota/<one-of-the-filenames>.ota | head -1   # expect 200
```

### 6. Point Zigbee2MQTT at it

Edit `/opt/zigbee2mqtt/data/configuration.yaml`:
```yaml
ota:
  zigbee_ota_override_index_location: http://127.0.0.1:8091/index.json
  disable_automatic_update_check: true
```
```bash
sudo systemctl restart zigbee2mqtt
```
Then in the z2m UI, each device with a newer `fileVersion` shows an available update;
click **Update** per device. (See [Triggering OTA](#triggering-an-ota-from-the-cli).)

That's it — the timer keeps the index fresh every ~15 minutes with no further action.

---

## What install.sh creates

| Path | Owner / mode | Purpose |
|---|---|---|
| user `dfr-ota` | system, `nologin` | runs both services unprivileged |
| `/opt/dfr-ota-server/dfr_ota_sync.py` | root | the tool (copied from this repo) |
| `/var/lib/dfr-ota/` | `dfr-ota` | state root (owned so the atomic swap works) |
| `/var/lib/dfr-ota/pub/` | `dfr-ota` | **served** dir: `index.json` + `ota/*.ota` |
| `/var/lib/dfr-ota/.staging/` | `dfr-ota` | build-then-swap staging |
| `/etc/dfr-ota/repos.conf` | `root:dfr-ota` 640 | repos to track |
| `/etc/dfr-ota/token` | `root:dfr-ota` 640 | read-only GitHub PAT |
| `/etc/dfr-ota/env` | root | `BASE_URL`, `BIND_ADDR`, `SERVE_PORT` |
| `/etc/systemd/system/dfr-ota-{sync.service,sync.timer,serve.service}` | root | the units |

`repos.conf`/`token` are group-readable by `dfr-ota` because the sync process reads them
in-process as that user. `env` is read by systemd as root before dropping privileges.

---

## Configuration reference

**`/etc/dfr-ota/env`** (read by both services):
```ini
BASE_URL=http://127.0.0.1:8091   # URLs written into the served index (device fetch source)
BIND_ADDR=127.0.0.1              # address the static server binds
SERVE_PORT=8091
```
> Use `127.0.0.1`, **not** `localhost`: the server binds IPv4 only, but `localhost` can
> resolve to IPv6 `::1` first (z2m/Node, curl) → connection refused.

**`/etc/dfr-ota/repos.conf`** — one `owner/repo` per line; `#` comments allowed.

**`/etc/dfr-ota/token`** — the fine-grained PAT, single line, `chmod 640`, `chgrp dfr-ota`.

CLI (used by the unit; also runnable by hand as root):
```
dfr_ota_sync.py --base-url URL [--repos FILE] [--token-file FILE]
                [--serve-dir DIR] [--staging-root DIR]
```
Defaults: `--repos /etc/dfr-ota/repos.conf`, `--token-file /etc/dfr-ota/token`,
`--serve-dir /var/lib/dfr-ota/pub`, `--staging-root /var/lib/dfr-ota/.staging`.
Exits non-zero if any repo failed (skipped repos are not failures).

---

## Running as a service

Three units:

| Unit | Type | Role |
|---|---|---|
| `dfr-ota-serve.service` | long-running | static server on `127.0.0.1:8091` serving `/var/lib/dfr-ota/pub` |
| `dfr-ota-sync.timer` | timer | fires the sync `OnBootSec=2min`, then every `15min` |
| `dfr-ota-sync.service` | oneshot | the actual pull → verify → publish run |

Common operations:
```bash
# status / logs
systemctl status dfr-ota-serve.service dfr-ota-sync.timer
journalctl -u dfr-ota-sync.service -n 50 --no-pager
systemctl list-timers dfr-ota-sync.timer

# force a sync now (don't wait for the timer)
sudo systemctl start dfr-ota-sync.service

# stop / start the server
sudo systemctl restart dfr-ota-serve.service

# pause automatic syncing (e.g. during maintenance)
sudo systemctl stop dfr-ota-sync.timer      # ...later: start again
```

---

## Maintenance

**Add a new device** — add its `owner/repo` to `/etc/dfr-ota/repos.conf`, then
`sudo systemctl start dfr-ota-sync.service`. Its image appears once that repo has a
GitHub Release. No z2m config change (one combined index).

**Update the tool** — copy the new `dfr_ota_sync.py` to the Pi and re-install:
```bash
rsync -av --exclude .git ./dfr-ota-server/ lino@<pi>:~/dfr-ota-server/
ssh lino@<pi> 'cd ~/dfr-ota-server && sudo ./install.sh && sudo systemctl start dfr-ota-sync.service'
```

**Rotate the token** — overwrite `/etc/dfr-ota/token` (keep `root:dfr-ota` 640) and run a
sync. Revoke the old PAT on GitHub.

**Retire an image** — removing a repo from `repos.conf` does **not** drop its already-
served image (entries carry forward as last-good). To force-remove: stop the timer,
delete the stale entry from `/var/lib/dfr-ota/pub/index.json` and its
`/var/lib/dfr-ota/pub/ota/<file>.ota`, then start the timer again.

**Move to real LAN exposure** (if another host must fetch OTA) — set `BIND_ADDR=0.0.0.0`
and `BASE_URL=http://<pi-lan-ip>:8091` in `/etc/dfr-ota/env`, update the z2m override URL
to match, `systemctl restart dfr-ota-serve.service` + re-sync + restart z2m.

---

## Triggering an OTA from the CLI

The z2m UI is the normal path. To check/trigger over MQTT (broker creds are in the z2m
config's `mqtt:` block):
```bash
# check availability (non-destructive)
mosquitto_pub -h localhost -u <mqttuser> -P <mqttpass> \
  -t zigbee2mqtt/bridge/request/device/ota_update/check -m '{"id":"<ieee-or-friendly-name>"}'
# ...watch: mosquitto_sub -h localhost -u <mqttuser> -P <mqttpass> -t 'zigbee2mqtt/bridge/response/device/ota_update/#' -v

# apply the update (downloads + reboots the device; a few minutes)
mosquitto_pub -h localhost -u <mqttuser> -P <mqttpass> \
  -t zigbee2mqtt/bridge/request/device/ota_update/update -m '{"id":"<ieee-or-friendly-name>"}'
```

---

## How images get into the index (firmware side)

Each firmware repo publishes into this index via its own
`.github/workflows/release-ota.yml`, triggered by a **`v*`** tag. That workflow builds
the firmware, wraps it into a `.ota` (manufacturer/image-type identity), updates its
committed `ota/index.json`, and publishes a GitHub Release asset. This aggregator then
pulls it.

Firmware-CI requirements (all needed, or the release build fails only in clean CI):
- Pin the PlatformIO platform to the **pioarduino** fork (the official registry lacks the
  ESP32-C6 board) — `platform = https://github.com/pioarduino/platform-espressif32/releases/download/55.03.31-2/platform-espressif32.zip`.
- Pin `actions/setup-python` to **3.13** (the pioarduino platform rejects 3.14).
- Native host tests link `-lm` (needed on Linux CI; `lroundf` is otherwise undefined).
- Tag with the `v` prefix (`git tag -a v1.0.0 -m ...`) — a bare `1.0.0` won't trigger the workflow.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl http://localhost:8091/...` hangs/refuses but `127.0.0.1` works | IPv6: `localhost`→`::1`, server is IPv4-only. Use `127.0.0.1` everywhere (BASE_URL + z2m). |
| Sync fails `PermissionError ... /var/lib/dfr-ota -> ...old` | Serve dir must be a subdir of a dfr-ota-owned dir. It's `/var/lib/dfr-ota/pub` — don't point `--serve-dir` at `/var/lib/dfr-ota` directly. |
| Sync: `<repo>: index fetch failed: HTTP 401/403` | Token missing/expired/wrong scope. Check `/etc/dfr-ota/token` (Contents:read on that repo) + perms `root:dfr-ota 640`. |
| Sync: `<repo>: no release/index — skipping` | That repo has no GitHub Release yet (or no `ota/index.json`). Normal; publish a release. |
| z2m shows no update | Wrong `zigbee_ota_override_index_location` (must be `http://127.0.0.1:8091/index.json`), or z2m not restarted, or device already current. |
| Two devices, one gets the wrong image | (Fixed) served files are namespaced by `(mfg,imageType)`. If it recurs, confirm `pub/ota` has distinct `<mfg>-<itype>-*.ota` files. |

Health check one-liner:
```bash
systemctl is-active dfr-ota-serve.service; curl -s http://127.0.0.1:8091/index.json | python3 -m json.tool
```

---

## Development

```bash
pip install pytest
python3 -m pytest tests/ -q     # pure logic: parse/rewrite/verify/merge/sync_once (+ fakes)
```
Runtime is stdlib-only; `pytest` is a dev/CI dependency only. CI runs the suite on push
(`.github/workflows/ci.yml`).
