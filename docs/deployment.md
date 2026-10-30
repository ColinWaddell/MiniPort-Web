# Deployment

## Layout

- `env/` — Python virtual environment (all dependencies)
- `web/dist/` — frontend build; served by the server at `/`
- `data/` — runtime state: `miniport.db` (SQLite/WAL), `recordings/`,
  `sweeps/`, `scan-presets.json` (legacy)
- `packaging/` — systemd unit + example config file

## Install from scratch

```bash
cd /home/mrcolin/Git/MiniPort-Web
python3 -m venv env
env/bin/pip install -e '.[dev,server]'
cd web && npm ci --no-fund --no-audit && npm run build && cd ..
env/bin/pytest            # quick sanity: ~40 s, simulated responses only
```

## Service configuration

Precedence (weakest first): built-in defaults → `--config <file>` JSON →
`EB200_*` environment variables → CLI flags. Copy
`packaging/miniport-web.json.example` to `/etc/miniport-web.json` (or point
`--config` into the repo) and keep it under version control minus secrets
(there are none; the app holds no credentials).

| Key | Env var | Default | Meaning |
|---|---|---|---|
| `web_host` | `EB200_WEB_HOST` | `127.0.0.1` | web bind address |
| `web_port` | `EB200_WEB_PORT` | `8000` | web port |
| `receiver_host` | `EB200_HOST` | `10.0.0.240` | EB200 address |
| `receiver_port` | `EB200_PORT` | `5555` | EB200 SCPI port |
| `poll_interval` | `EB200_POLL_INTERVAL` | `0.3` | state poll, seconds |
| `data_dir` | `EB200_DATA_DIR` | `<repo>/data` | SQLite + files |
| `log_level` | `EB200_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |
| `log_format` | `EB200_LOG_FORMAT` | `text` | `text` or `json` (structured) |
| `level_retention_days` | `EB200_LEVEL_RETENTION_DAYS` | `7` | level-log rows |
| `sweep_retention_days` | `EB200_SWEEP_RETENTION_DAYS` | `30` | stored sweep files |
| `recording_retention_days` | `EB200_RECORDING_RETENTION_DAYS` | `30` | WAV files |

## Systemd

```bash
sudo cp packaging/miniport-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now miniport-web
systemctl status miniport-web
journalctl -u miniport-web -f
```

The unit binds `0.0.0.0` (trusted LAN) via `EB200_WEB_HOST` and restarts
automatically (`Restart=always`, 3 s). The receiver connection recovers on
its own: the driver reconnects with bounded retries and re-identifies the
device when it returns. Verify from another machine:

```bash
curl http://mrpinchy:8330/api/health   # if EB200_WEB_PORT=8330 is set
```

Checklist after a restart: `systemctl is-active miniport-web`, no failed
units (`systemctl --failed`), the web port listens
(`ss -tlnp | grep <port>`), `GET /api/health` from outside the box, and the
badge turns green in a browser.

The audio path additionally needs the receiver's UDP traffic in:

```bash
sudo ufw allow from 10.0.0.240 to any proto udp comment 'eb200 audio'
```

Without it the stream connects but delivers no packets (the panel shows a
hint when that happens).

## Docker (optional)

`Dockerfile` (multi-stage: node build → python runtime) is provided, but
this host has no Docker engine, so the image has NOT been built or run
here — treat it as unverified until exercised on a machine with Docker:

```bash
docker build -t miniport-web .
docker run -p 8000:8000 -v miniport-data:/app/data --env EB200_HOST=10.0.0.240 miniport-web
```

## Updating

```bash
git pull            # or merge locally as usual
env/bin/pip install -e '.[dev,server]'
cd web && npm ci && npm run build && cd ..
sudo systemctl restart miniport-web
env/bin/pytest -m integration   # with the server stopped!
```

## Security notes

- The server binds localhost by default; the LAN binding is opt-in and the
  receiver API must not be exposed to the public internet (no
  authentication by design of this LAN tool).
- The browser only talks to this server; the EB200 TCP session is held by
  the server process alone.
- `data/` contains only recordings and measurement data, no credentials.