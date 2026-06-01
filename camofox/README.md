# camofox/ — anti-detect Firefox adapter (opt-in)

A wrapper around [redf0x1/camofox-browser](https://github.com/redf0x1/camofox-browser) (MIT) — REST API on `:9378` (host) → `:9377` (container) wrapping the Camoufox stealth browser engine. **BYO source: it's not bundled.**

## Why opt-in

Camoufox brings ~500 MB of patched Firefox binary + downloads ~150 MB on first run. Most users don't need stealth browsing.

## Install

The source isn't bundled — clone it + set a key once, then start it:

```bash
git clone https://github.com/redf0x1/camofox-browser camofox/src
echo "CAMOFOX_API_KEY=$(head -c24 /dev/urandom | base64 | tr -d '/+=' | head -c32)" >> .env
./install.sh --with-camofox      # → docker compose --profile camofox up -d --build (builds ./camofox/src)
```

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

## Use it

Any client that reads `CAMOFOX_URL` can route stealth-fetch tools through it. Set:
```bash
CAMOFOX_URL=http://localhost:9378
```
in `.env`. The key is passed as `Authorization: Bearer $CAMOFOX_API_KEY`.

## Persistent profiles

Browser profiles + cookies persist at `camofox/data/` (mounted into container). Wipe with `rm -rf camofox/data/` if you want a clean slate.

## License

Wrapper code is MIT (redf0x1/camofox-browser).
Underlying Camoufox engine is MPL-2.0 (daijro/camoufox). MPL is file-level copyleft — using Camoufox via the REST wrapper is fine for any downstream use.
