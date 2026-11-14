# ENSv2 registrar accounting

ENSv2 went live on Ethereum mainnet on October 27, 2026. The ENS DAO executed transaction [`0xed4ea910…a741f`](https://etherscan.io/tx/0xed4ea91015df1d27e2018471ea1f5961a1df57334635f8b85b0d04a2b69a741f) at block **26,282,117**. That transaction removed the v1 controllers (including `0x59e16f…ce547`, the NameWrapper and `0x283af0…eb7f5`) from the v1 BaseRegistrar. It added `Graveyard` and `ETHRenewerV1` as controllers and transferred BaseRegistrar ownership to `ETHRenewerV1`. The first v2 fee event is at block 26,282,334.

Sources inspected:

- [ensdomains/contracts-v2](https://github.com/ensdomains/contracts-v2), tag `v2-mainnet-deployment-1`, and branch `deploy/mainnet-v2-20261002` at `0f10caf5`. The branch has `contracts/deployments/mainnet/addresses.md` and the DAO calls in `contracts/deployments/mainnet/dao/proposal.md`.
- `src/registrar/{AbstractETHRegistrar,ETHRegistrar,ETHRenewerV1,StandardRentPriceOracle}.sol` and `script/deploy-constants.ts`.
- Mainnet logs, receipts and `eth_call` reads for the addresses below.

## Fee contracts

| Contract | Address | Deployed | Role |
| --- | --- | --- | --- |
| `ETHRegistrar` | `0xd6d8369e55c94ae8afa714e6cf7ab5be54d5cc81` | 26,183,999 | Registers new v2 `.eth` names and renews v2 names |
| `ETHRenewerV1` | `0x56c447f308f463a01121c41e72b0e7e7557fcb68` | 26,184,001 | Renews v1 names that have not migrated; also extends the v1 BaseRegistrar expiry |

Both contracts report `BENEFICIARY() = 0x4f2083f5fbede34c2714affb3105539775f7fe64` and `rentPriceOracle() = 0x76f29609a363a8a1a2a6abe2e859c0f8916af469` (`StandardRentPriceOracle`).

The `mainnet-root-20261013` deployment namespace also has an earlier `ETHRegistrar` (`0xdd86ae…b02e8`) and `ETHRenewerV1` (`0xe038b7…c9b62b1`). The DAO handover did not authorize them, and only their constructor events (`OwnershipTransferred` and `RentPriceOracleUpdated`) were seen on chain. They are not in the inventory.

`BatchRegistrar` and `RootBatchRegistrar` reserve or migrate names on behalf of the deployer and collect no payment. Neither is in the inventory.

## Payment model

v2 has no `msg.value`. The registrar asks the oracle for a price in the chosen ERC‑20 `paymentToken`, then moves exactly that amount with `SafeERC20.safeTransferFrom(paymentToken, msg.sender, BENEFICIARY, amount)`:

- `register`: transfers `base + premium` and emits `NameRegistered(uint256 indexed tokenId, string label, address owner, IRegistry subregistry, address resolver, uint64 duration, IERC20 paymentToken, bytes32 indexed referrer, uint256 base, uint256 premium)`.
- `renew` / `renewBatch`: emits `NameRenewed(uint256 indexed tokenId, string label, uint64 duration, uint64 newExpiry, IERC20 paymentToken, bytes32 indexed referrer, uint256 amount)` for each name. A batch makes one transfer for the sum.

Nothing is refunded and every event amount is a real transfer, so v1's renewal-refund bug does not apply. Duration is part of the event, so v2 needs no expiry-history reconstruction, oracle replay or traces. The fixture test (`tests/fixtures/ensv2-receipts.json`) checks that the event amounts in four real receipts equal the USDC paid to the beneficiary. The receipts cover a $2,255.12 registration that includes a $2,247.12 premium, a `renewBatch` of two names paid with one transfer, a v1-name renewal through a forwarding contract that keeps its own fee, and a v2 renewal through an intent executor.

`StandardRentPriceOracle` prices in USD units and converts to each token through a fixed `numer/denom` ratio set by `PaymentTokenUpdated`. The mainnet deploy script whitelists only:

| Token | Address | Decimals |
| --- | --- | --- |
| USDC | `0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48` | 6 |
| DAI | `0x6b175474e89094c44da98b954eedeac495271d0f` | 18 |

The pipeline values both at $1 face value. It keeps amounts in 10⁻¹⁸ USD so USDC and DAI sum exactly. Any other token fails the chunk until it is reviewed and added to `PAYMENT_TOKENS` (oracle owners can add tokens).

## Legacy controller side effects

`ETHRenewerV1.renew` extends the v1 BaseRegistrar directly. It then calls `syncWrapper`, which briefly re-adds the NameWrapper as a controller and calls the 2023 wrapped controller's `renew(label, 0)` without value. That second step emits a v1 `NameRenewed(name, label, cost = 0, expires)` from `0x253553…2303b` and a second BaseRegistrar `NameRenewed` with an unchanged expiry. `syncWrapper` is also public.

After the handover, no v1 controller can collect fees. The pipeline therefore indexes v1 controller logs, BaseRegistrar expiry history and Chainlink ETH/USD prices only through block 26,282,117, inclusive. The zero-value sync events are never fetched, and each v1-name renewal fee is counted once, from the `ETHRenewerV1` event.

The handover day (2026-10-27) values its ETH revenue at the last ETH/USD price available at the handover block, with status `v1_cutoff` in `daily_eth_usd.csv`. Later days have status `not_indexed_after_v1` and no price. They have no ETH revenue, so their `total_revenue_usd` is the v2 stablecoin revenue alone.

## Outputs

- `daily_revenue.csv` gains `v2_registration_base_usd`, `v2_registration_premium_usd` and `v2_renewal_usd`, between `total_revenue_eth` and the rightmost `total_revenue_usd`. `total_revenue_eth` stays ETH-only. `total_revenue_usd` is ETH revenue at the daily Chainlink close plus v2 stablecoin revenue, rounded to the cent.
- `daily_revenue_by_source.csv` gains a `revenue_usd` column. v2 rows have `currency=USD` and blank `revenue_wei` and `revenue_eth`. v2 sources are zero-filled from the first day with a v2 fee.
- `daily_activity.csv` counts v1 and v2 registrations and renewals together.
- `name_events_v2/YEAR.csv` lists v2 events as `name,event,price_usd,currency,date`. The v1 `name_events/` files keep their ETH layout.
- `events.csv.gz` gains `currency`, `payment_token`, `token_id`, `base_units`, `premium_units`, `revenue_units` and `revenue_usd`. Token amounts are in each token's smallest unit.

## Journal upgrade

Older journals record the five-controller inventory. `update` (and resuming `run`) accepts an inventory that only appends ENSv2 controllers. It backfills their logs over the blocks already covered, from each deployment block, and inserts any fee events in one transaction with the new inventory. It records the backfill under `controller_additions` in the manifest. Any other inventory change is still rejected.
