# ENS historical revenue

Cryo extracts Ethereum mainnet ENS registration/renewal logs and historical transaction traces. The pipeline writes exact, cash-basis daily revenue in ETH, purchased durations, and event-level evidence. No wallet or signing key is used.

The included backfill is complete for blocks **7,000,000–26,034,446**, ending September 22, 2026 at 17:13:59 UTC. It contains **5,280,551 events** and **64,990.401113873541302993 ETH** of corrected revenue. Start with [data/daily_revenue.csv](data/daily_revenue.csv): **one row per UTC day, one ETH column per revenue type, total ETH, and `total_revenue_usd` at the far right**. USD uses the day's Chainlink closing price where available; historical gaps are described below. Purchased durations and unique daily counts are in [data/daily_activity.csv](data/daily_activity.csv). Boundary UTC days are flagged in the detailed source table even though the requested block range is complete.

The renewal-event bug would otherwise add **381,825.582650650660072397 ETH** of refunded/repeatedly counted value to revenue. See the independently traced bulk-renewal example in the [accounting research](research/contract-accounting.md). The refreshed snapshot passes 41 pipeline tests; [September 22 validation](research/validation-2026-09-22.json) reconciles block coverage, CSV checksums, exact totals, event counts, durations, yearly name exports and daily USD conversion. The dashboard build and test also pass. [Original publication validation](research/final-validation.json) remains a record of the September 16 snapshot.

## Download the snapshot evidence

Published data has one home: the repository contains the final daily and yearly CSV outputs and `data/manifest.json`; the [snapshot-2026-09-22 release](https://github.com/gskril/ens-data/releases/tag/snapshot-2026-09-22) contains only `data/raw/`, the resumable `data/journal.sqlite` checkpoint, and the detailed `data/events.csv.gz` audit. Release checksums and archive validation accompany that evidence. Derived outputs are not duplicated as release attachments or inside its archive.

For analysis, clone the repository or download its CSVs directly; no release download is needed. To reproduce or extend the pipeline, restore the matching evidence as well. Requirements: Python 3.12 or newer, [uv](https://docs.astral.sh/uv/), Git, and the GitHub CLI (`gh`). Allow at least 20 GB of free disk for archives, restored data, dependencies and temporary exports.

The September 22 evidence matches the `snapshot-2026-09-22` repository tag, which includes all final CSVs and their checksums. Pin that tag for a reproducible restore. The release notes and `ARCHIVE_VALIDATION.json` record its exact commit. The [September 16 release](https://github.com/gskril/ens-data/releases/tag/snapshot-2026-09-16) remains available as historical evidence at revision `72f639c`:

```sh
git clone https://github.com/gskril/ens-data.git
cd ens-data
git checkout snapshot-2026-09-22
uv sync --locked
mkdir -p releases
gh release download snapshot-2026-09-22 --repo gskril/ens-data \
  --dir releases --pattern 'snapshot-2026-09-22-evidence.tar.gz.part-*' --pattern SHA256SUMS --pattern ARCHIVE_VALIDATION.json
(cd releases && sha256sum -c SHA256SUMS)
cat releases/snapshot-2026-09-22-evidence.tar.gz.part-* | tar -xzf -
```

Extraction restores only raw evidence, the event audit and the journal. It does not overwrite repository CSVs or the manifest. Restore into a fresh clone to avoid overwriting an existing local journal or raw data. `SHA256SUMS` verifies the archive parts; `ARCHIVE_VALIDATION.json` records the matching repository commit, snapshot bounds, SQLite consistency, and individual archived file hashes. The repository manifest verifies the final CSVs and event audit together. Archives are split into 1 GiB parts.

## Reproduce the downloaded snapshot

Verify the restored CSVs against the manifest, offline:

```sh
uv run python - <<'PY'
import json
from pathlib import Path
from ens_data.pipeline import file_sha256
root = Path("data")
manifest = json.loads((root / "manifest.json").read_text())
assert manifest["status"] == "complete"
for name, expected in manifest["files_sha256"].items():
    assert file_sha256(root / name) == expected, name
print("All snapshot CSV checksums match")
PY
uv run pytest -q
```

`uv run ens-data export --output data` regenerates all revenue/activity CSVs and the event audit from the restored SQLite journal, using the saved daily prices, with no RPC calls. This scans all 5.28 million events and rewrites the large audit file, so allow several minutes. Save the original manifest before export if you want to compare regenerated hashes with the published values. The dated research validation reports retain the results for each published snapshot.

To reproduce acquisition from the blockchain itself, use `run` with explicit bounds and a separate output directory, as shown below. An Ethereum mainnet RPC must support historical logs, historical `eth_call`, block headers and `trace_transaction`. The Alchemy account tested for the snapshot supported these methods; a logs-only or non-archive endpoint is insufficient. Bring your own RPC URL: credentials are deliberately excluded from GitHub.

## Configure the RPC and run

```sh
# Create .env if absent, then edit it to set your own RPC URL:
test -f .env || cp .env.example .env
chmod 600 .env
# Edit .env before continuing. Load it in each new shell:
set -a
. ./.env
set +a
uv run ens-data run --output data --chunk-size 100000 \
  --log-request-size 10000 --requests-per-second 20 --concurrency 8
uv run ens-data prices --output data --log-request-size 10000 \
  --requests-per-second 20 --concurrency 4
```

The default range starts at block 7,000,000 (a conservative floor preceding the 2019 permanent registrar) and ends at the RPC's finalized block. Both CLI bounds are **inclusive**. The end block and its hash are frozen in the journal: repeating `run` resumes the same snapshot. Use `update` below to extend a restored snapshot, or another output directory for an independent range. The local `.env` is ignored and must not be committed. The rate settings above were tested with the supplied Alchemy account; reduce them to your provider's limits. See [RPC findings](research/alchemy.md).

The supplied public RPC's mainnet route is `https://evm.stupidtech.net/v1/1`; use `--requests-per-second 1 --concurrency 1 --log-request-size 2000` with it. It publishes a limit of 60 requests per minute. Cryo also makes provider metadata requests, so occasional throttling may still require a retry.

To independently recreate the exact published block range without its journal/cache:

```sh
uv run ens-data run --start-block 7000000 --end-block 26034446 \
  --output runs/reproduction --chunk-size 100000 --log-request-size 10000 \
  --requests-per-second 20 --concurrency 8
uv run ens-data prices --output runs/reproduction --log-request-size 10000 \
  --requests-per-second 20 --concurrency 4
```

This is a full historical backfill and can take substantial RPC time. Restoring the snapshot avoids that work.

For a short historical run:

```sh
uv run ens-data run --start-block 18000000 --end-block 18000999 \
  --output runs/smoke --requests-per-second 10 --concurrency 4
uv run pytest -q
```

`uv run ens-data export --output data` regenerates the CSVs from the local journal without network access. During long runs, CSV snapshots are refreshed about every 30 minutes after a chunk finishes, and on normal completion or a handled error. Every completed chunk is checkpointed immediately, independently of CSV refreshes. The manifest is authoritative about completeness; a running backfill is not a completed historical dataset.

After starting a default `data` backfill, `uv run python -m ens_data.warm_empty` can accelerate retired-contract scans. It first proves that each remaining interval contains zero relevant events using a full-range Cryo query, then caches those empty chunks with a proof manifest. It never assumes a retirement date.

Keep exploratory runs under ignored `runs/`. The two fixtures under `tests/fixtures/` are used by regression tests. `research/` retains the accounting rationale, RPC findings and final verification summary; canonical raw data and the journal provide the detailed audit evidence.

## Append new finalized blocks

After restoring the full snapshot and configuring `ETH_RPC_URL`, run:

```sh
uv run ens-data update --output data --log-request-size 10000 \
  --requests-per-second 20 --concurrency 4
```

`update` checks Ethereum mainnet, verifies the saved end-block hash and contiguous revenue/expiry checkpoints, then freezes a new finalized end block. It acquires only ENS blocks after the previous cutoff, preserving the existing journal and expiry history. The original checkpoint size is retained, including a short final chunk. The command exports the combined history and refreshes daily Chainlink prices automatically. Existing price logs and historical state checks are reused; only missing price ranges are downloaded. The formerly partial final day is recomputed so it is not valued at the old snapshot cutoff. Explicit `--end-block NUMBER` bounds are inclusive and must be finalized.

If acquisition is interrupted, rerun the same `update` command: it resumes the pending frozen target before advancing again. If only the price refresh failed, run `uv run ens-data prices --output data` to retry it at the saved target. Until prices finish, positive-revenue USD cells are blank and `usd_conversion.status` is `not_indexed`. Before publishing, confirm `manifest.json` has `status: complete`, `last_error: null`, the intended end block/hash, and USD conversion metadata; then run the CSV checksum check above. The manifest records prior and new anchors under `extensions`.

Run only one writer per output directory. Updating changes the local `data/` files; the published GitHub release remains frozen. Restore into another clone first if you want to retain a local copy of the original snapshot. Never concatenate daily CSVs from separate block ranges: two ranges can share a UTC day, and the final-day closing price must be recomputed.

The controller inventory is explicit in `ens_data/contracts.py`. Updates cover those five controllers; future ENS deployments or pricing changes require an inventory/accounting review and tests. A changed inventory is rejected for an existing journal so historical gaps cannot be silently skipped. Chainlink phase transitions are discovered automatically.

Update validation: all 39 tests pass, including interrupted resumes, short checkpoint boundaries, anchor/coverage rejection and incremental price caches. A mainnet check acquired blocks 25,991,400–25,991,586, appended through 25,992,586, and matched an independent extraction of the whole interval: 66 events, 0.689861679719419163 ETH, and identical revenue, source, activity and price CSVs. Offline export reproduced all five CSV hashes. These small exploratory exports are not part of the published snapshot; the commands can reproduce the check under `runs/`.

To publish a newer snapshot, commit and push the daily and yearly name event CSVs, manifest, code and documentation. Stop the writer and close/checkpoint SQLite, then run `python scripts/package_release.py releases/SNAPSHOT_DATE` from the repository root. The packager includes only `data/events.csv.gz`, `data/journal.sqlite` and `data/raw/`, rejects overlap with Git-tracked data, creates 1 GiB parts, and verifies every archived file against its source hash. Upload the generated parts, `SHA256SUMS`, and `ARCHIVE_VALIDATION.json` to a **new dated GitHub release**. Record the matching repository commit in its restore instructions; do not attach final CSVs or bundle them in the archive. Keep prior snapshots and research validation as historical evidence, and validate each new snapshot before publishing.

## Files

| File | Contents |
| --- | --- |
| `data/daily_revenue.csv` | One row per UTC day; revenue-type columns in ETH, total ETH, and rightmost `total_revenue_usd` |
| `data/daily_eth_usd.csv` | Closing ETH/USD price, quote timestamp/age, feed phase, block/log evidence, and availability status |
| `data/daily_revenue_by_source.csv` | Detailed UTC date × source table; exact wei/ETH, counts, duration, refund corrections and coverage flags |
| `data/daily_activity.csv` | One row per UTC day: registration/renewal counts and duration totals, without duplicate premium rows |
| `data/events.csv.gz` | Compressed CSV: one row per controller event, including name, transaction, log index, expiry, duration, reported/corrected fees and accounting evidence |
| `data/name_events/YYYY.csv` | Plain CSV: `name,event,price_eth,date`, one row per registration or renewal, in block/log order |
| `data/manifest.json` | Requested range, finalized anchor hash, completed ranges, status, totals and CSV checksums |
| `data/raw/` | Resumable Cryo Parquet evidence and optional timestamp metadata |
| `data/journal.sqlite` | Transactional checkpoint and exact integer event journal; payloads use JSON or `Z1`-prefixed zlib JSON, never SQLite numeric sums |

`name_events/YYYY.csv` files are split by UTC year and regenerated by `run`, `update`, and the offline `export` command alongside the other CSVs, with a SHA-256 checksum in the manifest. Names include the `.eth` suffix; `event` is `register` or `renew`; `price_eth` is the corrected total fee (including registration premiums), rendered with exactly 18 decimal places; `date` is the UTC date (`YYYY-MM-DD`). This is historical activity, not a current ownership list. Years larger than 95 MiB are automatically split into `YYYY-part1.csv`, `YYYY-part2.csv`, etc., keeping each file below GitHub’s 100 MiB limit without splitting CSV records.

The main CSV columns, in order, are `date`, `registration_base_eth`, `registration_premium_eth`, `registration_combined_legacy_eth`, `renewal_eth`, `total_revenue_eth`, and `total_revenue_usd`. Total ETH is the exact sum of the four source columns, calculated in integer wei before decimal formatting.

Revenue sources are:

- `registration_base`: the duration-dependent base rent, for events with an explicit base/premium split.
- `registration_premium`: the one-time temporary premium, including legitimate large premiums. It is never capped or discarded as an outlier.
- `registration_combined_legacy`: older events' exact combined registration fee. Those events **do not expose the base/premium split**. Missing breakdowns are not fabricated or assigned a zero premium.
- `renewal`: the actual fee retained by the controller, checked against its oracle result and refund in the execution trace.

`duration_seconds` measures purchased time; **duration is not a separate revenue source**. Registration duration is `expires - block.timestamp`; renewal duration comes from successive base-registrar expiries, or the matching controller invocation's calldata when history is unavailable. Premium rows in the detailed source table carry zero duration, since the premium does not purchase additional time. Registration base already covers the entire purchased duration, so do not add duration-priced rent a second time. Use `daily_activity.csv` for unique event counts: base and premium rows in the detailed table refer to the same registrations.

The detailed source table preserves canonical `revenue_wei` amounts and `revenue_eth` renderings. All ETH columns use exactly 18 decimal places; no floating-point money arithmetic is used. `reported_renewal_cost_wei` preserves the erroneous original event value, and `renewal_overstatement_wei` records the removed excess. Fees are recognized on their payment day, not amortized over the registration term.

## Daily USD valuation

`total_revenue_usd = total_revenue_eth × closing ETH/USD price`, rounded to cents (half up) using integer arithmetic. The closing price is the last Chainlink update available before the next UTC midnight. For the unfinished final day, the cutoff is the snapshot timestamp (**2026-09-22 17:13:59 UTC** in the current dataset). This values the day's ETH receipts at one daily price; it does not sum transaction-time USD values.

`ens-data prices` follows the historical [Chainlink ETH/USD feed](https://data.chain.link/feeds/ethereum/mainnet/eth-usd) across all seven aggregator phases, discovers exact activation blocks, and seeds each new phase with its existing answer. The legacy phase-two adapter reads the phase-one aggregator, so its updates come from that underlying contract. Cryo logs and historical state checks are cached under `data/raw/daily_price_logs/` and `data/raw/daily_prices/`. A closing price in each phase is independently checked against historical contract state. See [Chainlink's historical-data documentation](https://docs.chain.link/data-feeds/historical-data).

The available feed lineage begins on **2020-01-15**, followed by an inactive period through **2020-04-07**. Quotes older than 24 hours are marked `stale` and excluded from conversion. In this snapshot, **333 revenue-bearing dates lack a usable price**, so their USD values are blank; zero ETH revenue remains `0.00` USD. The price CSV records these gaps explicitly. The USD sum therefore covers only priced dates. No alternate price provider is silently substituted.

Normal offline `ens-data export` reuses the saved daily prices. A fresh extraction has blank USD values for positive revenue until `ens-data prices` is run. Original ETH fees and renewal corrections do not change.

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

Full runs zero-fill days/sources without events. Partial runs include only observed dates, with source columns showing the acquired subtotal; consult the manifest before treating them as full-day totals. In `daily_revenue_by_source.csv`, `coverage=partial_day` flags the two boundary dates of a block interval and all rows of an incomplete backfill. Only fully acquired interior dates are marked `complete_day`. The journal and raw files belong to one anchored history. Only `update` advances its end block; do not mix unrelated journals or caches.

For Alchemy, the optional Transfers API supplies block timestamps in bulk; **transfer amounts are never used for revenue**. Missing timestamps fall back to Cryo block headers. This avoids millions of individual header requests. Logs and accounting traces are always extracted by Cryo.

References inspected:

- [Cryo](https://github.com/paradigmxyz/cryo), source `559b65455d7ef6b03e8e9e96a0e50fd4fe8a9c86`; runtime pinned to PyPI `0.3.2` and tested against its actual schemas.
- [gskril/ens-indexer](https://github.com/gskril/ens-indexer/tree/d76eddbef0e369a843fa92266cbaeeb1b3d66ece), especially controller ABIs/configuration and the separation of registrar and controller events.
- [ENS contracts](https://github.com/ensdomains/ens-contracts/tree/121dc232df06be28e5eabb34ebfc4bc788498c05), including deployed compiler metadata.
- [Alchemy limits and validation](research/alchemy.md).
