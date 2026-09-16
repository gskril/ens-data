# ENS historical revenue

Cryo extracts Ethereum mainnet ENS registration/renewal logs and historical transaction traces. The pipeline writes exact, cash-basis daily revenue in ETH, purchased durations, and event-level evidence. No wallet or signing key is used.

The included backfill is complete for blocks **7,000,000–25,991,586**, ending September 16, 2026. It contains **5,274,437 events** and **64,954.896599198722267329 ETH** of corrected revenue. Start with [data/daily_revenue.csv](data/daily_revenue.csv): **one row per UTC day, one ETH column per revenue type, and total daily revenue in the rightmost column**. Purchased durations and unique daily counts are in [data/daily_activity.csv](data/daily_activity.csv). Boundary UTC days are flagged in the detailed source table even though the requested block range is complete.

The renewal-event bug would otherwise add **381,825.548935822465311040 ETH** of refunded/repeatedly counted value to revenue. See the independently traced bulk-renewal example in the [accounting research](research/contract-accounting.md). All 26 tests passed, and [final validation](research/final-validation.json) reconciles block coverage, CSV checksums, exact totals, event counts and durations.

## Download the full snapshot

The private [snapshot-2026-09-16 release](https://github.com/gskril/ens-data/releases/tag/snapshot-2026-09-16) holds the full `data/` snapshot, including the compressed event CSV, raw Cryo evidence and SQLite checkpoint. Git contains the smaller daily CSVs, manifests, source code, tests and research. GitHub blocks Git files over 100 MiB and limits individual release assets to less than 2 GiB, so the archive is split into 1 GiB parts. Credentials, runtime environments, logs and temporary SQLite sidecars are excluded.

From a fresh clone, authenticate with an account that can access the private repository, then restore the data:

```sh
mkdir -p releases
gh release download snapshot-2026-09-16 --repo gskril/ens-data \
  --dir releases --pattern 'snapshot-2026-09-16.tar.gz.part-*' --pattern SHA256SUMS \
  --pattern daily-revenue-wide-update.tar.gz --pattern DAILY_CSV_SHA256SUMS
(cd releases && sha256sum -c SHA256SUMS && sha256sum -c DAILY_CSV_SHA256SUMS)
cat releases/snapshot-2026-09-16.tar.gz.part-* | tar --exclude='data/validation-*' -xzf -
tar -xzf releases/daily-revenue-wide-update.tar.gz
```

Extraction restores the frozen snapshot and overwrites matching `data/` files; use a fresh clone to preserve any newer local runs. The exclusion skips obsolete spot-check folders in the original archive. The small update archive applies the one-row-per-day CSV layout and updated manifest over the original snapshot. Archive checksums are supplied alongside the assets; the restored `data/manifest.json` also contains checksums for each final CSV. The release also provides `daily_revenue.csv` as a standalone download. See GitHub's [large-file guidance](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github) and [release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases).

## Run

```sh
uv sync --locked
# Set ETH_RPC_URL in your environment, or load your private .env:
set -a
. ./.env
set +a
uv run ens-data run --output data --chunk-size 100000 \
  --log-request-size 10000 --requests-per-second 20 --concurrency 8
```

The default range starts at block 7,000,000 (a conservative floor preceding the 2019 permanent registrar) and ends at the RPC's finalized block. Both CLI bounds are **inclusive**. The end block and its hash are frozen in the journal: repeating the same command resumes the same snapshot. Use another output directory for a new snapshot/range. The local `.env` is ignored and must not be committed.

The supplied public RPC's mainnet route is `https://evm.stupidtech.net/v1/1`; use `--requests-per-second 1 --concurrency 1 --log-request-size 2000` with it. It publishes a limit of 60 requests per minute. Cryo also makes provider metadata requests, so occasional throttling may still require a retry.

For a short historical run:

```sh
uv run ens-data run --start-block 18000000 --end-block 18000999 \
  --output runs/smoke --requests-per-second 10 --concurrency 4
uv run pytest -q
```

`uv run ens-data export --output data` regenerates the CSVs from the local journal without network access. During long runs, CSV snapshots are refreshed about every 30 minutes after a chunk finishes, and on normal completion or a handled error. Every completed chunk is checkpointed immediately, independently of CSV refreshes. The manifest is authoritative about completeness; a running backfill is not a completed historical dataset.

After starting a default `data` backfill, `uv run python -m ens_data.warm_empty` can accelerate retired-contract scans. It first proves that each remaining interval contains zero relevant events using a full-range Cryo query, then caches those empty chunks with a proof manifest. It never assumes a retirement date.

Keep exploratory runs under ignored `runs/`. The two fixtures under `tests/fixtures/` are used by regression tests. `research/` retains the accounting rationale, RPC findings and final verification summary; canonical raw data and the journal provide the detailed audit evidence.

## Files

| File | Contents |
| --- | --- |
| `data/daily_revenue.csv` | One row per UTC day; revenue-type columns in ETH and rightmost `total_revenue_eth` |
| `data/daily_revenue_by_source.csv` | Detailed UTC date × source table; exact wei/ETH, counts, duration, refund corrections and coverage flags |
| `data/daily_activity.csv` | One row per UTC day: registration/renewal counts and duration totals, without duplicate premium rows |
| `data/events.csv.gz` | Compressed CSV: one row per controller event, including name, transaction, log index, expiry, duration, reported/corrected fees and accounting evidence |
| `data/manifest.json` | Requested range, finalized anchor hash, completed ranges, status, totals and CSV checksums |
| `data/raw/` | Resumable Cryo Parquet evidence and optional timestamp metadata |
| `data/journal.sqlite` | Transactional checkpoint and exact integer event journal; payloads use JSON or `Z1`-prefixed zlib JSON, never SQLite numeric sums |

The main CSV columns, in order, are `date`, `registration_base_eth`, `registration_premium_eth`, `registration_combined_legacy_eth`, `renewal_eth`, and `total_revenue_eth`. The total is the exact sum of the four revenue columns, calculated in integer wei before decimal formatting.

Revenue sources are:

- `registration_base`: the duration-dependent base rent, for events with an explicit base/premium split.
- `registration_premium`: the one-time temporary premium, including legitimate large premiums. It is never capped or discarded as an outlier.
- `registration_combined_legacy`: older events' exact combined registration fee. Those events **do not expose the base/premium split**. Missing breakdowns are not fabricated or assigned a zero premium.
- `renewal`: the actual fee retained by the controller, checked against its oracle result and refund in the execution trace.

`duration_seconds` measures purchased time; **duration is not a separate revenue source**. Registration duration is `expires - block.timestamp`; renewal duration comes from successive base-registrar expiries, or the matching controller invocation's calldata when history is unavailable. Premium rows in the detailed source table carry zero duration, since the premium does not purchase additional time. Registration base already covers the entire purchased duration, so do not add duration-priced rent a second time. Use `daily_activity.csv` for unique event counts: base and premium rows in the detailed table refer to the same registrations.

The detailed source table preserves canonical `revenue_wei` amounts and `revenue_eth` renderings. All ETH columns use exactly 18 decimal places; no floating-point money arithmetic is used. `reported_renewal_cost_wei` preserves the erroneous original event value, and `renewal_overstatement_wei` records the removed excess. Fees are recognized on their payment day, not amortized over the registration term. USD conversion is not included.

## Accounting bug

The 2023 wrapped controller (`0x253553…2303b`) emits `NameRenewed.cost = msg.value`, even after refunding `msg.value - price.base`. Its event can overstate renewal revenue. Both values are ETH wei; this is **not a USD-unit field**. See [research/contract-accounting.md](research/contract-accounting.md) for deployed source, transaction evidence and the correction.

The default fast path reconstructs the deployed oracle's exact base price from purchased duration, immutable rent rates, and historical Chainlink rounds in transaction/log order. Every round must be present and the series must reconcile with the feed's start/end state. Phase-change windows and missing duration history fall back to traces. One affected transaction per name-length price band per chunk is also traced independently; its fee must match the reconstructed price. `--trace-only` disables this optimization and traces all affected renewals.

For traced renewals, the extractor reads the actual controller invocation's duration, immediate refund and oracle return, and verifies:

```text
retained fee = call value - refund = oracle base price
```

The wrapped event equals the call value. With complete oracle history, subtract the exact reconstructed base price to recover the refund guaranteed by the deployed function. Other controllers report retained fees correctly; their duration is recovered from the two base registrars' expiry histories, including migrations and updates by other authorized controllers. If history is missing, the extractor falls back to traces and also checks the reported fee. Whenever both duration sources are available they must agree. Failed call subtrees are excluded. Repeated names and multiple renewals inside a bulk transaction are matched individually. Missing required evidence or mismatches fail the chunk; the exporter never silently treats an uncorrected event as accurate revenue.

## Coverage and provenance

The five-controller inventory includes the two 2019 controllers omitted from the supplied `ens-indexer` configuration. Deployment floors for the three newer controllers are taken from that reference and ENS deployment artifacts. Older addresses are also listed in [ENS proposal EP 6.19](https://docs.ens.domains/dao/proposals/6.19/).

| Controller | Address | Event schema |
| --- | --- | --- |
| 2019 launch | `0xf0ad5cad05e10572efceb849f6ff0c68f9700455` | combined registration cost |
| 2019 update | `0xb22c1c159d12461ea124b0deb4b5b93020e6ad16` | combined registration cost |
| 2020 legacy | `0x283af0b28c62c092c9727f1ee09c02ca627eb7f5` | combined registration cost |
| 2023 wrapped | `0x253553366da8546fc250f225fe3d25d0c782303b` | base + premium; renewal refund bug |
| 2025 unwrapped | `0x59e16fccd424cc24e280be16e11bcd56fb0ce547` | base + premium + referrer; corrected renewal event |

Scope: permanent-registrar `.eth` controller fees on Ethereum. Excludes 2017 auction deposits/burns, separate short-name auctions, secondary-market sales, gas, subname registrations, DNS imports and contracts outside this inventory. BaseRegistrar, NameWrapper and referral-forwarder events are not counted again as revenue. This measures gross controller fee collections, not subsequent treasury withdrawals or referral-program expenses.

Full runs zero-fill days/sources without events. Partial runs include only observed dates, with source columns showing the acquired subtotal; consult the manifest before treating them as full-day totals. In `daily_revenue_by_source.csv`, `coverage=partial_day` flags the two boundary dates of a block interval and all rows of an incomplete backfill. Only fully acquired interior dates are marked `complete_day`. The journal and raw files belong to one immutable snapshot; do not mix caches from different runs.

For Alchemy, the optional Transfers API supplies block timestamps in bulk; **transfer amounts are never used for revenue**. Missing timestamps fall back to Cryo block headers. This avoids millions of individual header requests. Logs and accounting traces are always extracted by Cryo.

References inspected:

- [Cryo](https://github.com/paradigmxyz/cryo), source `559b65455d7ef6b03e8e9e96a0e50fd4fe8a9c86`; runtime pinned to PyPI `0.3.2` and tested against its actual schemas.
- [gskril/ens-indexer](https://github.com/gskril/ens-indexer/tree/d76eddbef0e369a843fa92266cbaeeb1b3d66ece), especially controller ABIs/configuration and the separation of registrar and controller events.
- [ENS contracts](https://github.com/ensdomains/ens-contracts/tree/121dc232df06be28e5eabb34ebfc4bc788498c05), including deployed compiler metadata.
- [Alchemy limits and validation](research/alchemy.md).
