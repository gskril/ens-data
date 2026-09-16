# Renewal overstatement in the wrapped registrar controller

## Finding

The deployed 2023 controller `0x253553366Da8546fC250F225fe3d25d0C782303b` emits the amount sent into `renew`, including the amount it has already refunded. The error is in the event's reported cost, not an extra fee retained by the contract.

The authoritative source is the compiler metadata embedded in [ENS's WrappedETHRegistrarController deployment artifact](https://github.com/ensdomains/ens-contracts/blob/121dc232df06be28e5eabb34ebfc4bc788498c05/deployments/mainnet/WrappedETHRegistrarController.json). The renewal function is extracted locally in [WrappedETHRegistrarController-renew.sol](WrappedETHRegistrarController-renew.sol). Its logic checks the incoming ETH against the oracle base price, renews the name, returns the excess to the caller, then emits `msg.value` as the event's cost. Refunding ETH does not change the value of `msg.value`.

The later controller at `0x59E16fcCd424Cc24e280Be16E11Bcd56fb0CE547` instead emits the oracle base price. See its [deployed artifact](https://github.com/ensdomains/ens-contracts/blob/121dc232df06be28e5eabb34ebfc4bc788498c05/deployments/mainnet/ETHRegistrarController.json) and the local [renewal source](ETHRegistrarController-renew.sol).

## Real transaction

In [transaction 0x6162687e…ef6dc](https://etherscan.io/tx/0x6162687e58d0df792ae72c46c75b658cc9a4db4a7a897710e534ed16c2eef6dc), `cengizter.eth` was renewed for 31,536,000 seconds:

| Quantity | Wei | ETH |
| --- | ---: | ---: |
| Reported event cost / controller call value | 3089717340120558 | 0.003089717340120558 |
| Actual retained fee / oracle base return | 3029134647177018 | 0.003029134647177018 |
| Refunded excess / overstatement | 60582692943540 | 0.000060582692943540 |

The `data/validation-wrapped` run covers blocks 18,000,000–18,000,999 inclusive on 2023-08-26. It contains 423 registration/renewal events, including 39 affected renewals. Correcting them removes 4,679,979,790,350,976 wei (0.004679979790350976 ETH). Corrected fee collections across all sources are 5.319095922311675670 ETH. This is a **partial UTC day**, not a full day's ENS revenue. The event CSV and raw Cryo traces retain the evidence.

### Amplification in bulk renewals

The largest individual corrections found in the full backfill occur in [transaction 0xea15c4af…998a0](https://etherscan.io/tx/0xea15c4af346390953a7afa578f31c29b94653584afcca054af31bbcc44e998a0), dated 2025-03-25. Its 18 renewal events report a combined **287.508250911322946087 ETH**, while the actual retained fees total **2.776308749486226318 ETH**. The same refunded ETH can fund subsequent calls, so summing each invocation's `msg.value` counts that ETH repeatedly. The combined overstatement is **284.731942161836719769 ETH**.

All 18 reconstructed fees, refunds and purchased durations were independently checked against the original 218-row transaction trace and matched exactly. See [largest-correction-trace-validation.json](largest-correction-trace-validation.json) and [largest-correction-events.json](largest-correction-events.json). This illustrates why simply interpreting the field as wei, without subtracting each invocation's refund, is still insufficient.

## Why this is not USD

A conflicting external analytics implementation describes the wrapped renewal event as attoUSD. The deployed function instead emits Solidity's `msg.value`, denominated in wei. The oracle starts with USD-denominated pricing parameters but converts them to wei before returning the base/premium quote. The actual call, oracle output and refund reconcile in wei in the example above. We follow the deployed execution, not that external interpretation.

## Correction and safeguards

The execution-trace method obtains historical `trace_transaction` through Cryo and finds the successful call to the specific controller with the matching name. Decode the purchased duration from that invocation. Find the immediate controller-to-caller refund inside the same invocation, and the oracle price call inside it. Require exact integer equality between incoming value minus refund and the returned base price. Keep the original event cost and correction as separate columns.

Do not substitute the top-level transaction's value: a bulk-renewal transaction can contain several controller invocations and a separate outer refund. Do not subtract every outgoing transfer by the controller across the entire transaction. Do not estimate the fee with today's exchange rate, a daily ETH price, or an end-of-block `eth_call`: intra-block oracle updates can differ from the state at the original call. A current or parent-block quote is not proof of an exact historical fee.

Successful-looking descendants of reverted calls are excluded. Repeated renewals of one name are matched in invocation order to its events. Missing evidence, leftover renewal invocations, or mismatches stop the chunk instead of publishing an invented correction.

## Exact historical oracle reconstruction

To avoid hundreds of thousands of redundant trace requests, the default path also reconstructs the price from the deployed [ExponentialPremiumPriceOracle artifact](https://github.com/ensdomains/ens-contracts/blob/121dc232df06be28e5eabb34ebfc4bc788498c05/deployments/mainnet/ExponentialPremiumPriceOracle.json). Its address is `0x7542565191d074ce84fbfa92cae13acb84788ca9`, its immutable USD feed is `0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419`, and its per-second attoUSD rents by length are `[0, 0, 20294266869609, 5073566717402, 158548959919]`. The pipeline checks these getters against the deployment values.

The Solidity formula is integer arithmetic:

```text
base fee in wei = floor(rent_attoUSD_per_second × duration_seconds × 100000000 / ETH_USD_answer)
```

Cryo extracts `AnswerUpdated` logs from the active Chainlink aggregator. The pipeline seeds the history with the round immediately before the chunk, requires consecutive round IDs, and checks that the last round and answer exactly match the chunk's final feed state. It uses the latest update preceding the registration-controller event in `(block_number, transaction_index, log_index)` order. The price is neither interpolated nor averaged over a day. A proxy phase change, missing rounds or unavailable duration causes trace fallback. Solidity's byte-based `StringUtils.strlen` is reproduced, including non-ASCII labels.

Purchased renewal duration is obtained from successive base-registrar expiry events. The deployed wrapped controller refunds the emitted call value minus this exact base price before emitting `NameRenewed`, so a successful event proves that the calculated excess was refunded. The original event value and reconstructed refund remain visible in the CSV, along with `oracle_round_id`, `eth_usd_answer`, and `accounting_method`.

The reconstructed amounts matched **all 126** traced wrapped renewals in two independent historical samples: 124 in the 2023 sample (feed phase 6) and two in the 2026 sample (phase 7). See [oracle-validation.json](oracle-validation.json). Each backfill chunk additionally traces a renewal from every observed price band and requires exact agreement. `--trace-only` retains the full replay alternative.

## Legacy registration breakdown limitation

The old controller reports one combined registration cost. Reconstructing its base/premium split requires separately replaying its historical oracle configuration; applying the wrapped controller's immutable formula to all years is not justified. The downloaded logs already show `NewPriceOracle` changes at blocks 10,451,343, 14,098,763 and 14,678,435. The earlier oracle was Maker-based, and ENS's [treasury transition discussion](https://discuss.ens.domains/t/ep1-social-proposal-transfer-ens-treasury-and-contract-ownership/6307?page=4) explicitly distinguishes it from the later oracle. [Execute EP9](https://agora.ensdao.org/proposals/107166664722174233740232174220463354481004128961821575530758100250899337476509) then introduced the exponential-premium oracle at `0xcf7fe2e614f568989869f4aade060f4eb8a105be`.

This export preserves the exact combined legacy fee under `registration_combined_legacy`; it does not reconstruct those earlier component allocations. The limitation affects the breakdown, not the fee total. See [legacy-oracle-changes-observed.json](legacy-oracle-changes-observed.json) for example on-chain changes found during acquisition; it is an observed subset, not a complete deployment inventory.
