import polars as pl
from datetime import datetime, timezone

from ens_data.contracts import BASE_REGISTRARS, BASE_MIGRATED, BASE_REGISTERED, BASE_RENEWED
from ens_data.pipeline import Pipeline


def row(address, topic, expiry, index, tx="0x" + "ab" * 32):
    return dict(address=address, topic0=topic, topic1="0x" + "12" * 32,
                data="0x" + expiry.to_bytes(32, "big").hex(),
                block_number=10000000, log_index=index, transaction_hash=tx)


def test_migrations_reregistration_and_same_block_renewal(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, "unused")
    address = BASE_REGISTRARS[1][0]
    rows = [row(address, BASE_MIGRATED, 1000, 0), row(address, BASE_RENEWED, 1100, 1),
            row(address, BASE_REGISTERED, 3000, 2), row(address, BASE_RENEWED, 3200, 3)]
    monkeypatch.setattr(p, "collect", lambda *a, **kw: pl.DataFrame([r for r in rows if r["address"] in kw["contract"]]))
    updates, durations = p.expiry_history(10000000, 10000001)
    assert list(durations.values()) == [100, 200]
    assert updates[(address, "0x" + "12" * 32)] == 3200
    assert p.db.execute("SELECT count(*) FROM expiries").fetchone()[0] == 0  # not committed before accounting


def test_missing_history_is_unknown_and_registrars_do_not_collide(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, "unused")
    old, new = [x[0] for x in BASE_REGISTRARS]
    rows = [row(old, BASE_REGISTERED, 100, 0), row(new, BASE_REGISTERED, 200, 1),
            row(old, BASE_RENEWED, 300, 2), row(new, BASE_RENEWED, 300, 3)]
    monkeypatch.setattr(p, "collect", lambda *a, **kw: pl.DataFrame([r for r in rows if r["address"] in kw["contract"]]))
    _, durations = p.expiry_history(10000000, 10000001)
    assert len(durations) == 2
    assert sorted(durations.values()) == [100, 200]
    rows[:] = [row(new, BASE_RENEWED, 1000, 1)]
    _, durations = p.expiry_history(10000000, 10000001)
    assert durations == {}


def test_rpc_log_range_is_enforced_even_if_cryo_ignores_inner_request_size(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, "unused", log_request_size=10)
    seen = []
    def fake_collect(dataset, **kwargs):
        first, stop = map(int, kwargs["blocks"][0].split(":"))
        seen.append((first, stop))
        assert stop - first <= 10
        return pl.DataFrame({"block_number": list(range(first, stop)), "chain_id": [1] * (stop-first)})
    monkeypatch.setattr("ens_data.pipeline.cryo.collect", fake_collect)
    frame = p.collect("logs", "range", blocks=["100:125"], inner_request_size=10)
    assert seen == [(100, 110), (110, 120), (120, 125)]
    assert frame["block_number"].to_list() == list(range(100, 125))
    p.collect("logs", "range", blocks=["100:125"], inner_request_size=10)
    assert len(seen) == 3  # resume reuses complete output


def test_parallel_timestamp_pagination_is_complete_and_cached(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, "https://example.alchemy.com", rps=8, concurrency=4)
    calls = []
    class FakeRPC:
        def __init__(self, *args):
            pass
        def call(self, method, params):
            q = params[0]
            first, last = int(q["fromBlock"], 16), int(q["toBlock"], 16)
            calls.append((first, last, q.get("pageKey")))
            number = last if q.get("pageKey") else first
            result = {"transfers": [{"blockNum": hex(number), "metadata": {
                "blockTimestamp": datetime.fromtimestamp(number + 1000, timezone.utc).isoformat()}}]}
            if not q.get("pageKey"):
                result["pageKey"] = str(first)
            return result
    monkeypatch.setattr("ens_data.pipeline.RPC", FakeRPC)
    events = [{"controller": "0xabc", "block_number": b} for b in [100, 110, 120, 130]]
    result = p.transfer_timestamps(100, 140, events)
    assert result == {b: b+1000 for b in [100, 109, 110, 119, 120, 129, 130, 139]}
    assert len(calls) == 8
    assert p.transfer_timestamps(100, 140, events) == result
    assert len(calls) == 8
