# The website (nightshift-qa.github.io)

`site/` is the public website: plain HTML and one stylesheet, no build step and no scripts, so it
loads fast and search engines read every word. It is hosted free on GitHub Pages from the
`nightshift-qa` organisation's `nightshift-qa.github.io` repository.

## Preview

```
python -m http.server 8095 --directory site
```

## Before the first publish

Two links are placeholders until they exist:

- `__FORM_URL__`: the Google Form for pilot applications (set: "Nightshift QA: pilot applications", email alerts on).
- `__APP_URL__`: the app's fixed link from Tailscale Funnel (set: https://nightshift.taile6ca67.ts.net/).

Fill them in, then check none are left:

```
sed -i "s|__FORM_URL__|https://forms.gle/...|g; s|__APP_URL__|https://nightshift.example.ts.net/|g" site/*.html site/*/*.html
grep -rn "__FORM_URL__\|__APP_URL__" site || echo "all links set"
```

## Publish

The site's own history is split out of this repository and pushed to the Pages repository. No
force push is needed: the split is the same every time for the same commits.

```
git subtree split --prefix site -b site-publish
git push https://github.com/nightshift-qa/nightshift-qa.github.io.git site-publish:main
git branch -D site-publish
```

GitHub Pages serves it within a minute or two.

## New screenshots

With the hosted app running on fake data:

```
NS_EMAIL=... NS_PASSWORD=... uv run python deploy/site/make_assets.py --app http://localhost:8091
```

It rewrites `site/assets/shot-*.jpg`, `og.png` (the picture shown when the link is shared) and
`apple-touch-icon.png`. If a picture's size changes, update its `width`/`height` in `site/index.html`.

## Search engines

- Google Search Console: add the property `https://nightshift-qa.github.io/`, verify with the HTML
  tag method (the `<meta name="google-site-verification">` tag goes in `site/index.html`'s head),
  then submit `sitemap.xml`.
- Bing Webmaster Tools can import the property from Search Console.
- Keep `sitemap.xml`'s `lastmod` dates current when a page changes.

## Desktop icon

`uv run python deploy/site/make_icon.py` redraws `launch/nightshift.ico` from the logo; then
`powershell -ExecutionPolicy Bypass -File launch\make-shortcut.ps1` puts "Nightshift QA" on the desktop.
