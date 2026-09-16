import copy
import json
from pathlib import Path

import pytest
from eth_abi import encode
from eth_utils import keccak

from ens_data.accounting import account_transaction, decode_log, eth, renewal_evidence, revenue_parts, duration_key, match_durations
from ens_data.contracts import CONTROLLERS, PRICE, REGISTERED, RENEW, RENEWED, RENEWED_REFERRER


WRAPPED = CONTROLLERS[3].address
CALLER = "0x" + "12" * 20
ORACLE = "0x" + "34" * 20


def call(path, to, payload, value=0, sender=CALLER, output="0x", kind="call", error=None):
    return dict(trace_address="_".join(map(str, path)), action_to=to, action_from=sender,
                action_input=payload, action_value=str(value), result_output=output,
                action_call_type=kind, error=error)


def renewal(path, name="test", value=120, price=100, duration=31536000):
    payload = RENEW + encode(["string", "uint256"], [name, duration]).hex()
    quote = PRICE + encode(["string", "uint256", "uint256"], [name, 0, duration]).hex()
    rows = [call(path, WRAPPED, payload, value),
            call([*path, 0], ORACLE, quote, sender=WRAPPED, kind="staticcall",
                 output="0x" + encode(["uint256", "uint256"], [price, 0]).hex())]
    if value > price:
        rows.append(call([*path, 1], CALLER, "0x", value-price, sender=WRAPPED))
    return rows


def event(name="test", cost=120, index=1, controller=WRAPPED):
    return dict(block_number=17_000_000, transaction_index=0, transaction_hash="0x"+"ab"*32,
                log_index=index, controller=controller, controller_name="wrapped_2023", kind="renewal", name=name,
                labelhash="0x"+keccak(text=name).hex(), expires=2_000_000_000, reported_cost_wei=cost,
                base_wei=None, premium_wei=None, referrer=None)


def test_refund_is_removed_from_revenue():
    row = account_transaction([event()], 1_700_000_000, renewal([]))[0]
    assert row["revenue_wei"] == 100
    assert row["reported_cost_wei"] == 120
    assert row["correction_wei"] == 20
    assert row["duration_seconds"] == 31536000


def test_bulk_repeated_name_matches_order_and_ignores_failed_subtrees():
    traces = renewal([0], value=120, price=100) + renewal([1], value=240, price=200)
    # Parent revert invalidates children even if the child itself has no error.
    traces += [call([2], CALLER, "0x", error="Reverted")] + renewal([2, 0], value=999, price=999)
    rows = account_transaction([event(index=5), event(cost=240, index=9)], 1_700_000_000, traces)
    assert [x["revenue_wei"] for x in rows] == [100, 200]
    assert [x["correction_wei"] for x in rows] == [20, 40]


def test_missing_or_inconsistent_traces_fail_closed():
    with pytest.raises(ValueError, match="Missing trace"):
        account_transaction([event()], 1_700_000_000, [])
    with pytest.raises(ValueError, match="reconcile"):
        account_transaction([event()], 1_700_000_000, renewal([])[:-1])
    with pytest.raises(ValueError, match="disagrees"):
        account_transaction([event(cost=121)], 1_700_000_000, renewal([]))


@pytest.mark.parametrize("controller", CONTROLLERS)
def test_all_registration_abis_preserve_split_and_big_integers(controller):
    huge = 2**120 + 1
    split = controller.registration_shape != "combined"
    types = ["string", "uint256", "uint256"] + (["uint256"] if split else [])
    values = ["alice", huge, 31] + ([1_800_000_000] if split else [])
    if controller.registration_shape == "referrer":
        types.append("bytes32")
        values.append(bytes(32))
    log = dict(address=controller.address, topic0=REGISTERED[controller.registration_shape],
               topic1="0x" + keccak(text="alice").hex(), data="0x" + encode(types, values).hex(),
               block_number=17_000_000, transaction_index=1, transaction_hash="0x"+"ab"*32, log_index=1)
    e = decode_log(log)
    e["revenue_wei"] = e["reported_cost_wei"]
    assert sum(value for _, value in revenue_parts(e)) == huge + (31 if split else 0)
    assert e["base_wei"] == (huge if split else None)
    if not split:
        assert revenue_parts(e)[0][0] == "registration_combined_legacy"


def test_renewal_referrer_schema():
    log = dict(address=CONTROLLERS[4].address, topic0=RENEWED_REFERRER,
               topic1="0x" + keccak(text="alice").hex(), data="0x" + encode(
                   ["string", "uint256", "uint256", "bytes32"], ["alice", 123, 1800000000, bytes(32)]).hex(),
               block_number=23_000_000, transaction_index=1, transaction_hash="0x"+"ab"*32, log_index=1)
    assert decode_log(log)["reported_cost_wei"] == 123
    log["topic0"] = RENEWED
    with pytest.raises(ValueError, match="ABI"):
        decode_log(log)


def test_exact_eth_rendering():
    assert eth(10**30 + 1) == "1000000000000.000000000000000001"
    assert eth(-1) == "-0.000000000000000001"


def test_real_mainnet_bulk_renewal():
    fixtures = Path(__file__).parent / "fixtures"
    raw = json.loads((fixtures / "bulk-renewal-traces.json").read_text())
    traces = [dict(trace_address="_".join(map(str, r["traceAddress"])), action_to=r.get("action", {}).get("to"),
                   action_from=r["action"].get("from"), action_input=r["action"].get("input"),
                   action_value=str(int(r["action"].get("value", "0x0"), 16)),
                   action_call_type=r["action"].get("callType"), error=r.get("error"),
                   result_output=r.get("result", {}).get("output")) for r in raw]
    evidence = renewal_evidence(traces)
    assert set(evidence) == {(WRAPPED, "0x" + keccak(text=name).hex()) for name in ["wenew", "wiifittrainer"]}
    assert all(v[0]["revenue_wei"] == 2098294406747828 for v in evidence.values())
    assert all(v[0]["duration_seconds"] == 31536000 for v in evidence.values())


def test_solidity_string_with_non_utf8_bytes_is_not_dropped():
    name = b"bad\xffname"
    log = dict(address=WRAPPED, topic0=RENEWED, topic1="0x" + keccak(name).hex(),
               data="0x" + encode(["bytes", "uint256", "uint256"], [name, 100, 1800000000]).hex(),
               block_number=18000000, transaction_index=0, transaction_hash="0x"+"ab"*32, log_index=1)
    e = decode_log(log)
    assert e["name_bytes_hex"] == "0x" + name.hex()
    assert e["reported_cost_wei"] == 100


def test_real_overstatement_is_refund_not_usd():
    fixture = json.loads((Path(__file__).parent / "fixtures/wrapped-refund.json").read_text())
    candidates = renewal_evidence(fixture["traces"])
    e = next(iter(candidates.values()))[0]
    assert e["call_value_wei"] == 3089717340120558
    assert e["revenue_wei"] == 3029134647177018
    assert e["refund_wei"] == 60582692943540
    assert e["duration_seconds"] == 31536000


def test_expiry_history_can_replace_traces_only_for_unaffected_controller():
    e = event(controller=CONTROLLERS[2].address, cost=100)
    durations = {duration_key(e): 1000}
    row = account_transaction([e], 1_700_000_000, [], durations)[0]
    assert row["duration_seconds"] == 1000
    assert row["revenue_wei"] == 100
    e["controller"] = WRAPPED
    with pytest.raises(ValueError, match="Missing trace"):
        account_transaction([e], 1_700_000_000, [], durations)


def test_trace_and_history_duration_disagreement_is_rejected():
    e = event()
    durations = {duration_key(e): 1}
    with pytest.raises(ValueError, match="duration disagrees"):
        account_transaction([e], 1_700_000_000, renewal([]), durations)


def test_oracle_proof_removes_refund_and_rejects_underpayment():
    e = event()
    durations = {duration_key(e): 1000}
    prices = {(e["transaction_hash"], e["log_index"]): dict(revenue_wei=100, oracle_round_id=123, eth_usd_answer=200000000000)}
    row = account_transaction([e], 1_700_000_000, [], durations, prices)[0]
    assert row["revenue_wei"] == 100
    assert row["correction_wei"] == 20
    assert row["accounting_method"] == "oracle_rounds_and_expiry_history"
    prices[(e["transaction_hash"], e["log_index"])]["revenue_wei"] = 121
    with pytest.raises(ValueError, match="exceeds"):
        account_transaction([e], 1_700_000_000, [], durations, prices)


def test_repeated_expiry_with_zero_duration_stays_distinct():
    first, second = event(index=2), event(index=4)
    prefix = duration_key(first)[:-1]
    history = {(*prefix, 1): 1000, (*prefix, 3): 0}
    durations = match_durations([first, second], history)
    assert durations[duration_key(first)] == 1000
    assert durations[duration_key(second)] == 0
