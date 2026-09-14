# Harmonics Analysis website

This directory is the static companion site for Harmonics Analysis. It is
published by the repository's GitHub Pages workflow and needs no build step.

## Preview locally

From the repository root:

```sh
python3 -m http.server 8000 --directory site
```

Then visit `http://localhost:8000`.

## Publish with GitHub Pages

The workflow in `.github/workflows/pages.yml` deploys the contents of this
directory whenever `main` changes. In GitHub, enable **Settings → Pages → Build
and deployment → Source: GitHub Actions** once. The resulting URL is normally
`https://fedoragobrowse-design.github.io/harmonics-analysis/`.

The SVG interface images are repository-owned visual documentation based on the
application's real controls and display language. Update them when a material
UI change affects the screenshots.
