"""Exact wrapped renewal prices from the deployed oracle and Chainlink rounds.

No interpolation, daily price or end-of-block price is used. Every round in a
window must be present; proxy phase-change windows fall back to execution traces.
"""
from bisect import bisect_right
import json

from eth_abi import decode

from .accounting import duration_key, raw_bytes
from .contracts import CONTROLLERS, signature

ORACLE = "0x7542565191d074ce84fbfa92cae13acb84788ca9"
FEED = "0x5f4ec3df9cbd43714fe2740f5e3616155c5b8419"
# Immutable constructor values in ENS's ExponentialPremiumPriceOracle artifact.
RATES = (0, 0, 20294266869609, 5073566717402, 158548959919)
ANSWER_UPDATED = signature("AnswerUpdated(int256,uint256,uint256)")
WRAPPED = CONTROLLERS[3].address


def solidity_length(data):
    offset, length = 0, 0
    while offset < len(data):
        byte = data[offset]
        offset += next((n for n, cap in [(1, 0x80), (2, 0xE0), (3, 0xF0), (4, 0xF8), (5, 0xFC)] if byte < cap), 6)
        length += 1
    return length


def fee_for(name_bytes, duration, answer):
    if answer <= 0 or duration < 0:
        raise ValueError("Invalid oracle price inputs")
    length = solidity_length(name_bytes)
    rate = RATES[min(max(length, 1), 5) - 1]
    return rate * duration * 10**8 // answer


def round_series(before, after, rows, start):
    """Return an ordered, gap-free feed history or None if tracing is required."""
    if before[0] >> 64 != after[0] >> 64:
        return None
    round_id, answer = before[:2]
    if answer <= 0:
        return None
    series = [((start - 1, 2**32, 2**32), round_id, answer)]
    for row in sorted(rows, key=lambda r: (r["block_number"], r["transaction_index"], r["log_index"])):
        native_round = int(row["topic2"], 16)
        update = int.from_bytes(raw_bytes(row["topic1"]), "big", signed=True)
        if native_round != (round_id & (2**64 - 1)) + 1 or update <= 0:
            return None
        round_id += 1
        answer = update
        series.append(((row["block_number"], row["transaction_index"], row["log_index"]), round_id, answer))
    return series if (round_id, answer) == tuple(after[:2]) else None


class OracleHistory:
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.directory = pipeline.raw / "oracle"
        self.directory.mkdir(exist_ok=True)

    def call(self, address, method, block):
        return raw_bytes(self.pipeline.rpc.call("eth_call", [dict(to=address, data=signature(method)[:10]), hex(block)]))

    def verify_config(self, block):
        path = self.directory / "configuration.json"
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["oracle"] != ORACLE or tuple(saved["rates"]) != RATES or saved["feed"] != FEED:
                raise ValueError("Unexpected cached oracle configuration")
            return
        if "0x" + self.call(WRAPPED, "prices()", block)[-20:].hex() != ORACLE:
            raise ValueError("Wrapped controller price oracle changed")
        if "0x" + self.call(ORACLE, "usdOracle()", block)[-20:].hex() != FEED:
            raise ValueError("Unexpected USD oracle")
        rates = tuple(int.from_bytes(self.call(ORACLE, f"price{n}Letter()", block), "big") for n in range(1, 6))
        if rates != RATES:
            raise ValueError("Unexpected immutable rent prices")
        from .pipeline import atomic_json
        atomic_json(path, dict(oracle=ORACLE, feed=FEED, rates=rates, verified_at_block=block))

    def prices(self, start, stop, events, durations):
        eligible = [e for e in events if e["controller"] == WRAPPED and e["kind"] == "renewal" and duration_key(e) in durations]
        if not eligible:
            return {}
        self.verify_config(stop - 1)
        path = self.directory / f"anchors-{start}-{stop}.json"
        if path.exists():
            anchors = json.loads(path.read_text())
        else:
            types = ["uint80", "int256", "uint256", "uint256", "uint80"]
            before = decode(types, self.call(FEED, "latestRoundData()", start - 1))
            after = decode(types, self.call(FEED, "latestRoundData()", stop - 1))
            aggregator = "0x" + self.call(FEED, "aggregator()", start - 1)[-20:].hex()
            anchors = dict(before=before, after=after, aggregator=aggregator)
            from .pipeline import atomic_json
            atomic_json(path, anchors)
        if anchors["before"][0] >> 64 != anchors["after"][0] >> 64:
            return {}  # Rare phase changes use original execution traces.
        frame = self.pipeline.collect("oracle_logs", f"{start}-{stop}", _dataset="logs", blocks=[f"{start}:{stop}"],
                                      contract=[anchors["aggregator"]], topic0=[ANSWER_UPDATED],
                                      inner_request_size=self.pipeline.log_request_size)
        series = round_series(anchors["before"], anchors["after"], frame.to_dicts(), start)
        if series is None:
            return {}
        positions = [r[0] for r in series]
        result = {}
        for event in eligible:
            index = bisect_right(positions, (event["block_number"], event["transaction_index"], event["log_index"])) - 1
            _, round_id, answer = series[index]
            name_bytes = raw_bytes(event.get("name_bytes_hex") or "0x" + event["name"].encode().hex())
            result[(event["transaction_hash"], event["log_index"])] = dict(
                revenue_wei=fee_for(name_bytes, durations[duration_key(event)], answer),
                oracle_round_id=round_id, eth_usd_answer=answer)
        return result
