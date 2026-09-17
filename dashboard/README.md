# ENS data explorer

A standalone Vite + TypeScript + React + Recharts app. No backend, database, RPC credentials, or Python dependencies. All dashboard dependencies and generated files stay in this directory; the deployment workflow lives in `.github/workflows/dashboard-pages.yml`.

```sh
cd dashboard
npm ci
npm run dev
```

`npm test` checks CSV parsing, UTC aggregation, inclusive date filters, missing prices, and reconciliation against the repository CSVs. `npm run build` type-checks and builds `dist/`; `npm run preview` serves the production build.

The dev/build commands copy only `data/daily_revenue.csv` and `data/daily_activity.csv` from the repository into ignored `public/data/`. Restart dev or rebuild after changing a CSV. Only `dist/` is published. Relative asset paths support the GitHub Pages repository subpath.

## Features and interpretation

- Revenue in ETH or USD, revenue sources, event counts, and purchased years.
- Inclusive UTC date filters and presets ending at the snapshot's latest date. The default and All time ranges begin on the first date with ETH revenue, registrations, or renewals, shared across all views. Earlier zero-activity dates remain available through the date inputs; switching views preserves the chosen range.
- Daily, Monday-based weekly, or monthly sums; line, stacked area, and stacked bar charts.
- Toggle series, inspect tooltips, view an accessible data table, export the filtered/aggregated view, or download the original CSVs.
- Missing USD prices remain null. An aggregated period containing a missing price is also null. The USD summary explicitly totals priced dates only.
- Edge periods include selected dates only; the snapshot's boundary days can be partial. No extrapolation. Purchased years are duration seconds / 31,557,600 (365.25 days).
- Browser calculations use floating-point numbers for visualization. Download canonical source CSVs for exact ETH amounts. Legacy combined fees are never represented as a known base/premium split.

## GitHub Pages

Pages uses GitHub Actions as its source. Pushes to `main` affecting this app, its two CSVs, or the workflow build and deploy automatically. Pull requests run the same tests/build without deploying. A manual workflow dispatch is also available. Data acquisition is separate: the site updates when refreshed daily CSVs are committed.

To configure a fresh fork, enable Pages with **Settings → Pages → Source → GitHub Actions**, then run the workflow. The site URL is `https://<owner>.github.io/<repository>/`.
