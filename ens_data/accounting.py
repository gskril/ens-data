"""Pure decoding and exact accounting. Missing evidence raises; it is never zero."""
from collections import defaultdict
from datetime import datetime, timezone

from eth_abi import decode
from eth_utils import keccak

from .contracts import BASE_FOR_CONTROLLER, BY_ADDRESS, PRICE, REGISTERED, RENEW, RENEWED, RENEWED_REFERRER, RENEW_REFERRER


def hexstr(value):
    if value is None:
        return None
    return ("0x" + bytes(value).hex()) if isinstance(value, (bytes, bytearray)) else value.lower()


def raw_bytes(value):
    return bytes.fromhex(value.removeprefix("0x")) if isinstance(value, str) else bytes(value)


def decode_named(types, payload):
    # Solidity strings can contain arbitrary bytes. Preserve the exact label even
    # if a historical caller bypassed client-side ENS normalization.
    values = decode(["bytes", *types[1:]], payload)
    return values[0], values


def duration_key(event):
    return (BASE_FOR_CONTROLLER[event["controller"]], event["transaction_hash"], event["labelhash"], event["expires"], event["log_index"])


def match_durations(events, history):
    """Pair each fee event with the nearest preceding base renewal log.

    A zero-duration renewal can repeat the preceding renewal's expiry in the
    same transaction. Expiry alone therefore cannot uniquely identify a call.
    """
    from bisect import bisect_left
    groups = defaultdict(list)
    for key, duration in history.items():
        groups[key[:-1]].append((key[-1], duration))
    for values in groups.values():
        values.sort()
    matched, consumed = {}, set()
    for event in events:
        if event["kind"] != "renewal":
            continue
        key = duration_key(event)
        candidates = groups.get(key[:-1], [])
        index = bisect_left(candidates, (key[-1], -1)) - 1
        if index >= 0:
            base_index, duration = candidates[index]
            identity = (*key[:-1], base_index)
            if identity in consumed:
                raise ValueError("Two controller events matched one base renewal")
            consumed.add(identity)
            matched[key] = duration
    return matched


def decode_log(row):
    address, topic = hexstr(row["address"]), hexstr(row["topic0"])
    controller = BY_ADDRESS.get(address)
    if controller is None:
        raise ValueError(f"Unknown controller {address}")
    if topic == REGISTERED[controller.registration_shape]:
        kind = "registration"
        types = ["string", "uint256", "uint256"]
        if controller.registration_shape != "combined":
            types.append("uint256")
        if controller.registration_shape == "referrer":
            types.append("bytes32")
        name_bytes, values = decode_named(types, raw_bytes(row["data"]))
        if controller.registration_shape == "combined":
            base, premium, cost, expires = None, None, values[1], values[2]
        else:
            base, premium, expires = values[1:4]
            cost = base + premium
    elif topic in (RENEWED, RENEWED_REFERRER):
        if (topic == RENEWED_REFERRER) != (controller.registration_shape == "referrer"):
            raise ValueError("Renewal ABI does not match controller")
        kind = "renewal"
        types = ["string", "uint256", "uint256"] + (["bytes32"] if topic == RENEWED_REFERRER else [])
        name_bytes, values = decode_named(types, raw_bytes(row["data"]))
        _, cost, expires = values[:3]
        base, premium = None, None
    else:
        return None  # Commitments and administrative events are not revenue.
    label = hexstr(row["topic1"])
    if label != "0x" + keccak(name_bytes).hex():
        raise ValueError("Event name does not match indexed labelhash")
    return dict(block_number=int(row["block_number"]), transaction_index=int(row["transaction_index"]),
                transaction_hash=hexstr(row["transaction_hash"]), log_index=int(row["log_index"]),
                controller=address, controller_name=controller.name, kind=kind,
                name=name_bytes.decode("utf-8", errors="backslashreplace"), name_bytes_hex="0x" + name_bytes.hex(),
                labelhash=label, expires=expires, reported_cost_wei=cost,
                base_wei=base, premium_wei=premium,
                referrer=hexstr(values[-1]) if types[-1] == "bytes32" else None)


def trace_path(row):
    value = row["trace_address"]
    if isinstance(value, list):
        return tuple(value)
    return tuple(int(x) for x in value.split("_") if x)


def successful_traces(rows):
    """A successful child of a reverted ancestor did not happen on chain."""
    failed = [trace_path(r) for r in rows if r.get("error")]
    return [r for r in rows if not any(trace_path(r)[:len(p)] == p for p in failed)]


def renewal_evidence(rows):
    """Read the actual call's oracle result, duration and immediate refund.

    Scope each refund to its controller invocation, including bulk renewals and
    repeated names. Preflight rentPrice calls are deliberately not candidates.
    """
    rows = successful_traces(rows)
    evidence = defaultdict(list)
    for call in sorted(rows, key=trace_path):
        address = hexstr(call.get("action_to"))
        data = hexstr(call.get("action_input")) or "0x"
        if address not in BY_ADDRESS or data[:10] not in (RENEW, RENEW_REFERRER):
            continue
        if call.get("action_call_type") != "call":
            continue
        types = ["string", "uint256"] + (["bytes32"] if data[:10] == RENEW_REFERRER else [])
        name, args = decode_named(types, raw_bytes(data)[4:])
        duration = args[1]
        path = trace_path(call)
        children = [r for r in rows if trace_path(r)[:-1] == path and len(trace_path(r)) == len(path) + 1]
        incoming = int(call["action_value"])
        refunds = [r for r in children if hexstr(r.get("action_from")) == address
                   and hexstr(r.get("action_to")) == hexstr(call.get("action_from"))
                   and r.get("action_call_type") == "call" and int(r["action_value"]) > 0]
        refund = sum(int(r["action_value"]) for r in refunds)
        quotes = []
        for child in children:
            payload = hexstr(child.get("action_input")) or "0x"
            if hexstr(child.get("action_from")) == address and payload[:10] == PRICE:
                quoted_name, quoted_args = decode_named(["string", "uint256", "uint256"], raw_bytes(payload)[4:])
                quoted_duration = quoted_args[2]
                output = raw_bytes(child.get("result_output") or "0x")
                if quoted_name == name and quoted_duration == duration and len(output) in (32, 64):
                    quotes.append(int.from_bytes(output[:32], "big"))
        net = incoming - refund
        if net < 0 or duration < 0 or len(quotes) != 1 or quotes[0] != net:
            raise ValueError(f"Cannot reconcile renewal oracle and refund for {name} at {path}")
        evidence[(address, "0x" + keccak(name).hex())].append(dict(duration_seconds=duration, revenue_wei=net,
                                             refund_wei=refund, call_value_wei=incoming,
                                             trace_address="_".join(map(str, path))))
    return evidence


def account_transaction(events, timestamp, traces, durations=None, prices=None):
    durations = durations or {}
    prices = prices or {}
    evidence = renewal_evidence(traces) if traces and any(e["kind"] == "renewal" for e in events) else {}
    result = []
    for original in sorted(events, key=lambda e: e["log_index"]):
        e = dict(original)
        e["timestamp"] = timestamp
        e["date"] = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        if e["kind"] == "registration":
            e.update(duration_seconds=e["expires"] - timestamp, revenue_wei=e["reported_cost_wei"],
                     refund_wei=None, call_value_wei=None, trace_address=None, correction_wei=0,
                     accounting_method="controller_event")
        else:
            candidates = evidence.get((e["controller"], e["labelhash"]), [])
            duration = durations.get(duration_key(e))
            price = prices.get((e["transaction_hash"], e["log_index"]))
            if not candidates and duration is not None and price is not None and BY_ADDRESS[e["controller"]].refund_bug:
                correction = e["reported_cost_wei"] - price["revenue_wei"]
                if correction < 0:
                    raise ValueError("Reconstructed oracle price exceeds call value")
                e.update(price, duration_seconds=duration, refund_wei=correction, correction_wei=correction,
                         call_value_wei=e["reported_cost_wei"], trace_address=None, accounting_method="oracle_rounds_and_expiry_history")
                result.append(e)
                continue
            if not candidates and duration is not None and not BY_ADDRESS[e["controller"]].refund_bug:
                e.update(duration_seconds=duration, revenue_wei=e["reported_cost_wei"],
                         refund_wei=None, call_value_wei=None, trace_address=None, correction_wei=0,
                         accounting_method="controller_event_and_expiry_history")
                result.append(e)
                continue
            if not candidates:
                raise ValueError(f"Missing trace for renewal {e['transaction_hash']}:{e['log_index']}")
            ev = candidates.pop(0)
            expected = ev["call_value_wei"] if BY_ADDRESS[e["controller"]].refund_bug else ev["revenue_wei"]
            if e["reported_cost_wei"] != expected:
                raise ValueError("Event cost disagrees with traced controller call")
            e.update(ev, correction_wei=e["reported_cost_wei"] - ev["revenue_wei"],
                     accounting_method="trace_oracle_and_refund")
            if duration is not None and duration != e["duration_seconds"]:
                raise ValueError("Trace duration disagrees with base registrar expiry history")
            if price is not None:
                if price["revenue_wei"] != e["revenue_wei"]:
                    raise ValueError("Traced fee disagrees with reconstructed oracle rounds")
                e.update(oracle_round_id=price["oracle_round_id"], eth_usd_answer=price["eth_usd_answer"])
        if e["duration_seconds"] < 0:
            raise ValueError("Negative duration")
        result.append(e)
    if any(evidence.values()):
        raise ValueError("Renewal traces lack matching events")
    return result


def eth(wei):
    """Exact fixed-point rendering without float or Decimal context rounding."""
    sign = "-" if wei < 0 else ""
    whole, fraction = divmod(abs(wei), 10**18)
    return f"{sign}{whole}.{fraction:018d}"


def revenue_parts(event):
    if event["kind"] == "renewal":
        return [("renewal", event["revenue_wei"])]
    if event["base_wei"] is None:
        return [("registration_combined_legacy", event["revenue_wei"])]
    return [("registration_base", event["base_wei"]), ("registration_premium", event["premium_wei"])]
