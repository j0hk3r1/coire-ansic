# camofox/

The `--with-camofox` adapter expects [Camoufox](https://github.com/daijro/camoufox)
source (or any compatible anti-detect-Firefox fork that ships a
`Dockerfile.ci`) cloned into `./src/`.

```bash
git clone https://github.com/daijro/camoufox camofox/src
./install.sh --with-camofox
```

If `camofox/src/` is empty or missing, `install.sh --with-camofox` skips the
profile with a warning instead of failing. This keeps `--all` installs
robust on machines where Camoufox isn't available or isn't desired.

Camoufox is GPL-licensed and not bundled with this repo. Bring your own.
