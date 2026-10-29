import csv
import json
from pathlib import Path

import polars as pl
import pytest
from eth_abi import encode
from eth_utils import keccak

from ens_data.accounting import account_transaction, decode_log, hexstr, revenue_parts
from ens_data.contracts import (CONTROLLERS, LEGACY_CONTROLLERS, PAYMENT_TOKENS, V1_HANDOVER_BLOCK,
                                V2_CONTROLLERS, V2_RENEWED, signature)
from ens_data.daily_prices import usd_amount
from ens_data.pipeline import Pipeline
from test_accounting import event as legacy_event, renewal as legacy_renewal


FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "ensv2-receipts.json").read_text())
V2 = {c.address for c in V2_CONTROLLERS}
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
BENEFICIARY = "0x4f2083f5fbede34c2714affb3105539775f7fe64"
TRANSFER = signature("Transfer(address,address,uint256)")
WRAPPED = LEGACY_CONTROLLERS[3]
REGISTRAR, RENEWER = V2_CONTROLLERS


def accounted(tx):
    events = [decode_log(row) for row in tx["logs"] if row["address"] in V2]
    return account_transaction([e for e in events if e], tx["block_timestamp"], [])


def test_mainnet_event_amounts_equal_stablecoin_paid_to_beneficiary():
    # Receipts include third-party fee forwarders and batched registrations.
    for tx in FIXTURE["transactions"]:
        rows = accounted(tx)
        assert rows
        paid = sum(int(log["data"], 16) for log in tx["logs"]
                   if log["address"] == USDC and log["topic0"] == TRANSFER
                   and "0x" + log["topic2"][-40:] == BENEFICIARY)
        assert sum(row["revenue_units"] for row in rows) == paid
        for row in rows:
            assert row["accounting_method"] == "v2_token_event"
            assert row["currency"] == "USDC" and row["revenue_wei"] is None
            assert row["labelhash"] == "0x" + keccak(bytes.fromhex(row["name_bytes_hex"][2:])).hex()
            assert int(row["token_id"]) >> 32 == int(row["labelhash"], 16) >> 32


def test_premium_registration_splits_base_and_premium_in_usd():
    tx = next(t for t in FIXTURE["transactions"] if t["transaction_hash"].startswith("0x0b95"))
    (row,) = [r for r in accounted(tx) if r["kind"] == "registration"]
    assert row["premium_units"] > 0
    assert row["expires"] == tx["block_timestamp"] + row["duration_seconds"]
    parts = dict(revenue_parts(row))
    assert set(parts) == {"v2_registration_base", "v2_registration_premium"}
    # USDC has 6 decimals; sources are held in 10^-18 USD.
    assert parts["v2_registration_base"] == row["base_units"] * 10**12
    assert sum(parts.values()) * 10**6 // 10**18 == row["revenue_units"]


def test_v1_renewer_fee_is_counted_once_and_wrapper_sync_is_dropped(tmp_path, monkeypatch):
    tx = next(t for t in FIXTURE["transactions"] if t["transaction_hash"].startswith("0xcbb0"))
    p = Pipeline(tmp_path, "unused")
    monkeypatch.setattr(p, "collect", lambda dataset, key, **kwargs: pl.DataFrame(
        [r for r in tx["logs"] if r["address"] in kwargs["contract"]], infer_schema_length=None))
    block = tx["logs"][0]["block_number"]
    assert block >= V1_HANDOVER_BLOCK
    wrapped = [r for r in tx["logs"] if r["address"] == WRAPPED.address]
    assert len(wrapped) == 1 and decode_log(wrapped[0])["reported_cost_wei"] == 0
    assert p.controller_events(WRAPPED, block, block + 1) == []
    (renewed,) = p.controller_events(RENEWER, block, block + 1)
    assert renewed["kind"] == "renewal" and renewed["revenue_units"] > 0
    # A paid legacy event after the handover would mean the inventory is wrong.
    paid = dict(wrapped[0], data="0x" + encode(["string", "uint256", "uint256"], ["x", 1, 2]).hex(),
                topic1="0x" + keccak(text="x").hex())
    monkeypatch.setattr(p, "collect", lambda *args, **kwargs: pl.DataFrame([paid]))
    with pytest.raises(ValueError, match="after the ENSv2 handover"):
        p.controller_events(WRAPPED, block, block + 1)


def v2_renewal_log(token=USDC, label="alice", amount=5_000_000, index=1, tx="0x" + "cd" * 32):
    return dict(address=REGISTRAR.address, topic0=V2_RENEWED,
                topic1="0x" + keccak(text=label).hex(), topic2="0x" + "00" * 32, topic3=None,
                data="0x" + encode(["string", "uint64", "uint64", "address", "uint256"],
                                   [label, 31_536_000, 1_900_000_000, token, amount]).hex(),
                block_number=V1_HANDOVER_BLOCK + 1, transaction_index=0, transaction_hash=tx, log_index=index)


def test_unreviewed_payment_token_and_mismatched_label_fail_closed():
    with pytest.raises(ValueError, match="Unreviewed ENSv2 payment token"):
        decode_log(v2_renewal_log(token="0x" + "11" * 20))
    with pytest.raises(ValueError, match="token id"):
        decode_log(dict(v2_renewal_log(), topic1="0x" + keccak(text="bob").hex()))
    assert set(PAYMENT_TOKENS) == {USDC, "0x6b175474e89094c44da98b954eedeac495271d0f"}


def test_combined_usd_total_prices_eth_and_adds_stablecoins_exactly():
    quote = dict(answer=200050000000, decimals=8, status="close")
    assert usd_amount(0, None, 5 * 10**17) == "0.50"
    assert usd_amount(10**18, quote, 5 * 10**15) == "2000.51"  # 2000.505 rounds half up.
    # Unknown ETH value still blanks the total rather than understating it.
    assert usd_amount(10**18, None, 10**18) == ""


def test_export_writes_v2_usd_columns_and_separate_name_events(tmp_path):
    p = Pipeline(tmp_path, "unused")
    v1 = account_transaction([legacy_event()], 1_793_083_535, legacy_renewal([]))[0]
    dai = "0x6b175474e89094c44da98b954eedeac495271d0f"
    v2 = [account_transaction([decode_log(v2_renewal_log(index=i, token=token, amount=amount))], 1_793_083_535, [])[0]
          for i, token, amount in [(2, USDC, 1_234_567), (3, dai, 10**18 + 1)]]
    for row in [v1, *v2]:
        p.db.execute("INSERT INTO events VALUES (?,?,?,?)",
                     (row["transaction_hash"], row["log_index"], row["block_number"], json.dumps(row)))
    p.db.execute("INSERT INTO chunks VALUES (?,?)", (17_000_000, 17_000_001))
    p.export(dict(start_block=17_000_000, end_block=17_000_000, start_timestamp=1_793_080_000, end_timestamp=1_793_090_000))
    (daily,) = csv.DictReader((tmp_path / "daily_revenue.csv").open())
    assert daily["total_revenue_eth"] == "0.000000000000000100"
    assert daily["v2_renewal_usd"] == "2.234567000000000001"
    assert daily["v2_registration_base_usd"] == "0.000000000000000000"
    assert list(daily)[-1] == "total_revenue_usd"
    details = {r["source"]: r for r in csv.DictReader((tmp_path / "daily_revenue_by_source.csv").open())}
    assert details["v2_renewal"]["currency"] == "USD" and details["v2_renewal"]["revenue_wei"] == ""
    assert details["v2_renewal"]["event_count"] == "2"
    assert details["renewal"]["revenue_usd"] == ""
    with (tmp_path / "name_events" / "2026.csv").open() as stream:
        assert [r["price_eth"] for r in csv.DictReader(stream)] == ["0.000000000000000100"]
    with (tmp_path / "name_events_v2" / "2026.csv").open() as stream:
        assert [(r["name"], r["price_usd"], r["currency"]) for r in csv.DictReader(stream)] == [
            ("alice.eth", "1.234567000000000000", "USDC"), ("alice.eth", "1.000000000000000001", "DAI")]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["v2_total_revenue_usd"] == "2.234567000000000001"
    assert manifest["total_revenue_eth"] == "0.000000000000000100"
    assert "name_events_v2/2026.csv" in manifest["files_sha256"]
    activity = next(csv.DictReader((tmp_path / "daily_activity.csv").open()))
    assert activity["renewal_count"] == "3"


class Chain:
    finalized = 105

    def call(self, method, params):
        return "0x1"

    def block(self, number):
        number = self.finalized if number == "finalized" else number
        return dict(number=hex(number), timestamp=hex(1_700_000_000 + number * 12), hash=f"0x{number:064x}")


def test_update_backfills_appended_v2_controllers_over_covered_blocks(tmp_path, monkeypatch):
    p = Pipeline(tmp_path, "unused", chunk_size=4)
    p.rpc = Chain()
    chunks = []

    def process(first, stop):
        chunks.append((first, stop))
        with p.db:
            p.db.execute("INSERT INTO chunks VALUES (?,?)", (first, stop))
            p.commit_expiries(first, stop, {})
        return 0

    monkeypatch.setattr(p, "process_chunk", process)
    monkeypatch.setattr("ens_data.daily_prices.enrich", lambda p: None)
    p.run(100, 105)
    # Simulate a journal created before ENSv2 was added to the inventory.
    info = json.loads(p.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()[0])
    info["controllers"] = [c.__dict__ for c in LEGACY_CONTROLLERS]
    with p.db:
        p.db.execute("UPDATE meta SET value=? WHERE key='run'", (json.dumps(info),))
    requested = []

    def controller_events(controller, start, stop):
        requested.append((controller.name, start, stop))
        if controller is not REGISTRAR:
            return []
        return [dict(decode_log(v2_renewal_log()), block_number=103)]

    monkeypatch.setattr(p, "controller_events", controller_events)
    monkeypatch.setattr(p, "block_timestamps", lambda events: {103: 1_793_083_535})
    p.rpc.finalized = 109
    p.update()
    assert requested == [(c.name, 100, 106) for c in V2_CONTROLLERS]
    assert chunks[-1] == (106, 110)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["controllers"] == [c.__dict__ for c in CONTROLLERS]
    assert manifest["controller_additions"][0]["backfilled_through_block"] == 105
    assert manifest["controller_additions"][0]["backfilled_events"] == 1
    assert manifest["v2_event_count"] == 1
    # The legacy inventory itself may never change under an existing journal.
    info = json.loads(p.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()[0])
    info["controllers"][0] = dict(info["controllers"][0], start=1)
    with p.db:
        p.db.execute("UPDATE meta SET value=? WHERE key='run'", (json.dumps(info),))
    with pytest.raises(ValueError, match="Controller inventory changed"):
        p.update()
