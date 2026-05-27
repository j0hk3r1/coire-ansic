# camofox/ — anti-detect Firefox adapter

Auto-fetched from [redf0x1/camofox-browser](https://github.com/redf0x1/camofox-browser) (MIT) by `install.sh --with-camofox`. REST API on `:9378` (host) → `:9377` (container) wrapping the Camoufox stealth browser engine.

## Why opt-in

Camoufox brings ~500 MB of patched Firefox binary + downloads ~150 MB on first run. Most users don't need stealth browsing — searxng + firecrawl handle 90 % of web-extract needs.

## Install

```bash
./install.sh --with-camofox
```

What happens:
1. `git clone https://github.com/redf0x1/camofox-browser camofox/src/`
2. Generates `CAMOFOX_API_KEY` in `.env` if missing
3. `docker compose --profile camofox up -d --build`

## Endpoints

| | |
|---|---|
| API base | `http://localhost:9378` (or `http://172.17.0.1:9378` from inside docker) |
| Health | `GET /health` |
| Create tab | `POST /tabs` |
| Snapshot | `GET /tabs/:tabId/snapshot` |
| Navigate | `POST /tabs/:tabId/navigate` |
| Click | `POST /tabs/:tabId/click` |
| noVNC viewer | `http://localhost:6080` (loopback only) |

See [redf0x1/camofox-browser README](https://github.com/redf0x1/camofox-browser) for full API.

## Hook into omo

omo's librarian agent uses `CAMOFOX_URL` env var to route stealth-fetch tools. Set:
```bash
CAMOFOX_URL=http://localhost:9378
```
in `.env` (already templated). API key gets passed in `Authorization: Bearer $CAMOFOX_API_KEY`.

## Persistent profiles

Browser profiles + cookies persist at `camofox/data/` (mounted into container). Wipe with `rm -rf camofox/data/` if you want a clean slate.

## License

Wrapper code is MIT (redf0x1/camofox-browser).
Underlying Camoufox engine is MPL-2.0 (daijro/camoufox). MPL is file-level copyleft — using Camoufox via the REST wrapper is fine for any downstream use.
