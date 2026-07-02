# dfr-ota-server

Pull-based Zigbee OTA aggregator for a private DFR-* device family. A systemd
timer fetches each configured repo's latest GitHub Release `.ota` + `ota/index.json`
(read-only token), verifies sha512, and serves one merged z2m override index on the LAN.

## Install (on the z2m Pi)
1. `git clone` this repo; `sudo ./install.sh`.
2. Put a fine-grained GitHub PAT (Contents: read on the DFR-* repos) in `/etc/dfr-ota/token`.
3. Edit `/etc/dfr-ota/repos.conf` (one `owner/repo` per line) and `/etc/dfr-ota/env`
   (`BASE_URL=http://localhost:8091` — z2m is co-located, loopback only).
4. `sudo systemctl start dfr-ota-sync.service` then `curl http://localhost:8091/index.json`.

## Zigbee2MQTT
```yaml
ota:
  zigbee_ota_override_index_location: http://localhost:8091/index.json
  disable_automatic_update_check: true
```
Trigger updates per device in the z2m UI. Runs `--base-url` from `/etc/dfr-ota/env`.

## Development

Install pytest for testing:
```bash
pip install pytest
```

Run tests:
```bash
python3 -m pytest tests/ -q
```

## Configuration

See `repos.conf.example` for the format of the repos configuration file.

Note: removing a repo from `repos.conf` does **not** automatically drop its
already-served image from the index — merged entries carry forward from the
last-good index. To retire an image, stop the timer, clear/edit the state in
`/var/lib/dfr-ota` (e.g. remove the stale `index.json` entry and `ota/` file),
and re-run the sync.
