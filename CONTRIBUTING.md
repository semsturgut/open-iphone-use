# Contributing

Read [the architecture](docs/architecture.md) and [security](SECURITY.md) before changing
capture or input. Preserve owner consent, and inspect real UI outcomes when claiming device
behavior.

## Checks

```sh
python3 -m venv .venv
.venv/bin/python -m pip install ./server
PYTHONPATH=server .venv/bin/python -m unittest discover -s server/tests -v
node --check server/oiu/web/app.js
sh -n setup.sh && bash -n install.sh
```

Installer checks must use temporary home and prefix directories so real client configs are
untouched. To check an installed CLI without adding a phone or sending input:

```sh
sh setup.sh --no-agents
.venv/bin/python server/tests/serve_smoke.py ~/.local/bin/open-iphone-use
```

Unit tests use fake connections. Record hardware results separately (device, iOS, Xcode, USB
or Wi-Fi, what was checked on the screen) in [the CoreDevice doc](docs/coredevice.md). A
delivered input is not proof that the UI changed.

Do not commit credentials, device identifiers or personal screenshots. Contributions are
licensed under Apache-2.0. Keep issues factual and respectful.
