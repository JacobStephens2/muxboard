# Product site (muxboard.stephens.page)

Static marketing page. Source of truth for the **package** install and threat
model is still the root `README.md` and `examples/deploy/`; this `index.html`
is the short public face (hero, feature cards, install teaser).

## Deploy to the droplet

Docroot is Apache `DocumentRoot /var/www/muxboard.stephens.page`. From a
checkout of this repo on the host:

```bash
rsync -a --delete \
  --exclude README.md \
  site/ /var/www/muxboard.stephens.page/
```

No build step. Edit `site/index.html` in the same PR when install/auth copy
changes, then rsync after merge (or from the local checkout).

Do not treat the live docroot as canonical - pull from git, then rsync.
