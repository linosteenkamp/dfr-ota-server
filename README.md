# dfr-ota-server

A pull-based Zigbee OTA aggregator for the DFR-DoorSensor project. This Python 3 application aggregates OTA releases from multiple GitHub repositories and serves them via HTTP on a local network for use by Zigbee2MQTT.

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
