# ENS dashboard reconciliation — 2026-09-17

The indexed ETH fees pass the user's **0.5% tolerance across the full comparable history**: our CSV totals 64,953.486738628322814336 ETH through September 15, 2026, versus 64,883.164177020834 ETH in the dashboard, a **+0.10838337%** difference. However, the recent period has a larger discrepancy and should not be described as fully reconciled. No canonical revenue or price data was changed to fit the dashboard.

## Sources and scope

- [User-supplied ENS Dashboard](https://datastudio.google.com/reporting/8785928a-71d5-4b17-9fea-fe1c937b064f/page/RoKgC).
- [Annual revenue & income page](https://datastudio.google.com/reporting/8785928a-71d5-4b17-9fea-fe1c937b064f/page/p_ovi4hg0opc).
- [Captured public values](dashboard-source.csv): daily chart values, annual scorecards, monthly tables for 2025/2026, and selected daily/period revenue scorecards. Blank fields mean not returned or unavailable, not zero.
- [Computed comparisons](dashboard-comparison.csv): 51 annual, monthly, selected daily and boundary-period comparisons against the repository CSVs. Positive differences mean our indexed fees exceed the dashboard.
- Local dataset: `data/manifest.json`, frozen at block 25,991,586 / September 16, 2026 17:39:35 UTC. The dashboard's latest non-null daily revenue was September 15, so the partially acquired September 16 is excluded throughout the main comparison.

The report was read anonymously in a browser on September 17, 2026, approximately 01:50–01:59 UTC. Values came from the report's own `batchedDataV2` responses, preserving the published chart fields and changing only its ordinary date filters. Its revenue datasource ID is `7d3a6ee0-a5aa-4795-b377-1d4dfe9aa7fd`. Requests used the report's `Europe/Berlin` setting; results have native calendar DATE fields. The historical exact ETH agreement supports matching those returned dates directly to our UTC date buckets.

Only public numerical observations are retained. Browser/session tokens, unrelated response metadata and temporary screenshots are not required for reproduction and are not committed. The source CSV is a frozen capture, not a live synchronization with the dashboard.

## ETH comparison

| Period | Dashboard ETH | Indexed ETH | Difference |
| --- | ---: | ---: | ---: |
| May 4, 2019–December 6, 2025 | 63,001.72266485 | 63,001.72266485 | Less than 0.000000001 ETH |
| Full year 2025 | 3,248.41195683 | 3,256.11833427 | +0.23723523% |
| December 7, 2025–September 15, 2026 | 1,881.44151217 | 1,951.76407378 | +3.73769587% |
| January 1–September 15, 2026 | 1,757.46225063 | 1,820.07843480 | +3.56287506% |
| Full comparable history | 64,883.16417702 | 64,953.48673863 | +0.10838337% |

Every individual year from 2019 through 2024 matches in ETH to floating-point precision. Each month from January through November 2025 also matches. December 1–6 daily spot checks match, while December 7 introduces the substantial divergence. The aggregate agreement through December 6 includes the wrapped-controller era and independently supports our renewal-refund correction; it does not imply every individual event has been compared to dashboard event-level data.

### Legacy-controller coverage

From December 7 onward, the daily registration-count difference exactly matches registrations from the still-used 2020 controller, `0x283af0b28c62c092c9727f1ee09c02ca627eb7f5`. Daily renewal-count differences also match legacy-controller renewals plus our zero-fee renewals, except for nine remaining count differences on May 26 and June 11, 2026. This was checked against decoded event payloads in the local journal, grouped by date and controller.

This strongly suggests the dashboard stopped including that controller's activity around December 7. The dashboard's underlying BigQuery SQL/job configuration was not available for inspection, so the pipeline cause is **inferred from the observed counts**, not directly verified.

Legacy-controller fees total **73.026230211893296368 ETH** from December 7 through September 15. The actual total ETH gap is **70.322561607675857211 ETH**. After accounting for those legacy fees, a residual **2.703668604217439157 ETH in the opposite direction** remains: the dashboard exceeds our non-legacy fees by that amount. This residual is below 0.5% of the period's revenue but is not fully explained. Missing legacy activity explains most, not all, of the discrepancy. Normal recent ingestion delay cannot explain a persistent difference in completed historical months.

Activity counts have a separate definition caveat: our audit keeps zero-fee renewal events. Counts can therefore differ while ETH revenue agrees. Do not treat a count mismatch alone as missing revenue.

## USD comparison

The user's accepted distinction is transaction-time ETH prices in BigQuery versus the final usable Chainlink price of each UTC day in this repository. That legitimately produces small valuation differences; exact USD equality is not expected. For example, 2024 is $13,187,120.94 in the dashboard versus $13,195,425.78 here, approximately 0.063% apart. Historical missing-price dates must also be aligned: the comparison leaves our aggregate USD blank whenever the period contains positive-revenue dates without a usable Chainlink price, rather than presenting a partial sum as a full total.

There is a separate, larger anomaly in recent dashboard prices. Each monthly January–August 2026 value satisfies:

```text
dashboard USD revenue / dashboard ETH revenue ≈ 3037.93820614548
```

Independent daily scorecard checks on December 7–15, January 1, February 1, May 26 and June 11 yield the same implied rate, despite substantially different Chainlink prices. For example:

| Date | Dashboard implied USD/ETH | Chainlink UTC closing USD/ETH |
| --- | ---: | ---: |
| 2025-12-07 | 3,037.93820615 | 3,055.57702700 |
| 2026-02-01 | 3,037.93820615 | 2,270.38000000 |
| 2026-06-11 | 3,037.93820615 | 1,669.90445413 |

June 2026 consequently shows **$576,419.27** on the dashboard versus **$338,994.63** here. This pattern is consistent with stale/fallback pricing, beyond the ordinary difference between transaction-time and closing prices. The precise source/feed failure is unverified. By September 15 the dashboard implied rate changes to $2,526.18793512, so the evidence does **not** establish that the constant rate persists through every day of the comparison.

For January 1–September 15, 2026, dashboard USD revenue is **$5,336,032.03**, versus **$3,964,450.68** here. These figures should not be treated as an aligned USD comparison until the dashboard's historical pricing is checked.

## Revenue versus income

Compare our cash-basis `total_revenue_eth` with dashboard **Revenue (ETH)** / `_eth_revenue_`, not **Income (ETH)** / `_eth_income_`. The historical ETH agreement verifies that mapping empirically. The dashboard also presents an earn-out/income series, which this repository does not calculate. [Nick Johnson's description](https://discuss.ens.domains/t/ens-financial-position/2961) discusses earning prepaid registration fees over time; this is distinct from our recording the retained fee on payment day. The dashboard's complete current income formula was not inspected.

Likewise, **Premium Income (USD)** in the dashboard is not automatically comparable to our explicit ETH registration-premium column: the dashboard valuation and recognition definitions need to be aligned, and older controller events only expose a combined fee in our dataset.

## Reproduce and refresh

From a clone of this repository:

```sh
python3 research/compare_dashboard.py
```

This regenerates `dashboard-comparison.csv` from the captured `dashboard-source.csv`, `data/daily_revenue.csv` and `data/daily_activity.csv` without RPC or browser access. Differences are divided by the dashboard's ETH total; the threshold applies to the absolute percentage difference. The CSV records USD coverage gaps explicitly.

To refresh the external observations, open the report, select matching date intervals, and export its published charts/tables. Keep native unrounded values where available, the selected start/end dates, and the distinction between Revenue and Income. Stop at the last fully available day in both datasets. Update the source capture and this report together; do not silently reinterpret old captured values as current dashboard state.

The canonical CSVs and snapshot release remain unchanged by this comparison. The unresolved recent dashboard differences are documented rather than used to adjust independently reconciled on-chain fees.
