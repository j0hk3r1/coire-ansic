# camofox/ — anti-detect Firefox adapter (BYO source)

## Why this is opt-in

The vanilla [Camoufox](https://github.com/daijro/camoufox) upstream is a
Python library (`pip install camoufox`) + a stealth-patched Firefox
binary. It does **not** ship a REST/HTTP server out of the box — only a
Playwright-protocol launcher.

The docker service in `docker-compose.yml` expects a REST wrapper exposed
on port 9377 (see `Dockerfile.ci`). That wrapper does not exist in the
public Camoufox repo — it has to come from a fork that adds it.

So `--with-camofox` is **not** included in `--all` and is **not** the
out-of-the-box experience. If you want it, you bring your own source.

## How to enable it

```bash
# 1. Put a camofox-with-REST-wrapper source tree at ./camofox/src/
#    (must contain Dockerfile.ci and serve an HTTP endpoint on :9377)
git clone <your-camofox-fork> camofox/src

# 2. Install with the flag
./install.sh --with-camofox

# 3. Or, if already installed:
COMPOSE_PROFILES=dashboard,camofox docker compose up -d --build
```

If `camofox/src/` is missing, `install.sh --with-camofox` skips the
profile with a clear warning instead of failing.

## Why we don't bundle it

- Camoufox is GPL-licensed; bundling a derived REST wrapper would impose
  GPL terms on this repo.
- Most users don't need anti-detect browsing — it's a niche feature for
  scraping behind detection. omo's librarian can use it via `CAMOFOX_URL`;
  otherwise it falls back to searxng or external services.

## Alternatives

If you want web browsing without the Camoufox dependency:
- `--with-searxng` for meta-search + page fetch
- `--with-firecrawl` for full JS-rendered page extraction (Playwright under the hood)
