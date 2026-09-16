# Alchemy range and trace validation — 2026-09-16

The [current Ethereum eth_getLogs reference](https://www.alchemy.com/docs/chains/ethereum/ethereum-api-endpoints/eth-get-logs) lists a 10-block free-tier limit, unlimited Ethereum block ranges for Pay As You Go/Enterprise, and a 150 MB response cap. An [older deep-dive page](https://www.alchemy.com/docs/deep-dive-into-eth_getlogs) describes an additional 10,000-log cap for large ranges versus unrestricted log counts over 2,000 blocks. These pages differ, so the configured account was also probed directly.

Observed with the user-supplied endpoint (credential omitted):

| Probe | Result |
| --- | --- |
| `eth_chainId` | `0x1` (Ethereum mainnet) |
| `eth_getLogs`, controller `0xf0ad5…00455`, blocks 7,500,000–8,500,000 inclusive | Success, 9,388 logs across 1,000,001 blocks |
| `trace_transaction`, `0x7cf60de73535ec4db4cbd9f0b8817526d3b25d8596419dd90493e669e2a2c1dc` | Success, 38 trace entries |
| Cryo extraction, blocks 17,000,000–17,000,999 | 331 revenue events; registration/renewal reconciliation passed |
| Cryo extraction, blocks 18,000,000–18,000,999 | 423 revenue events; wrapped renewal corrections reconciled |

The [Trace API documentation](https://www.alchemy.com/docs/reference/trace-api-quickstart) restricts trace access to paid tiers. The successful probe establishes that the provided key can access the needed method; the exact account plan was not inspected.

Large sparse queries work. The dense 2020 base-registrar migration produced a real `Response is too big` error, and a second response explicitly stated that this account permits unlimited log counts within 10,000 blocks or at most 10,000 logs for larger ranges. The exporter therefore enforces request lengths itself and automatically bisects failed ranges. The full backfill uses 100,000-block checkpoints and 10,000-block requests. Completed Parquet files are reused. Acquisition errors never mark a chunk complete.

Cryo's Python `collect()` runtime (0.3.2) was observed to send large block ranges despite `inner_request_size`. The exporter explicitly partitions the `blocks` argument before calling Cryo; a regression test checks the actual requested boundaries. Setting `--log-request-size 1000000` still allows a sparse million-block query, with automatic splitting on failure.

The [Transfers API](https://www.alchemy.com/docs/data/transfers-api/transfers-endpoints/alchemy-get-asset-transfers) supports pagination and `withMetadata`. The Alchemy path uses this only to fetch timestamps in bulk; fees remain derived from Cryo event and trace data. Missing metadata is filled from actual block headers. The endpoint URL is not stored in the data manifest or research artifacts.
