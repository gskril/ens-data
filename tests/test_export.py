import csv
import json
import gzip
import zlib

from ens_data.pipeline import Pipeline, load_event
from test_accounting import event, renewal
from ens_data.accounting import account_transaction


def test_export_reconciles_and_keeps_incomplete_ranges_explicit(tmp_path):
    p = Pipeline(tmp_path, "unused")
    e = account_transaction([event()], 1_700_000_000, renewal([]))[0]
    p.db.execute("INSERT INTO events VALUES (?,?,?,?)", (e["transaction_hash"], e["log_index"], e["block_number"], json.dumps(e)))
    p.db.execute("INSERT INTO chunks VALUES (?,?)", (17_000_000, 17_000_001))
    info = dict(start_block=17_000_000, end_block=17_000_002, start_timestamp=1_700_000_000, end_timestamp=1_700_000_100)
    p.export(info)
    rows = list(csv.DictReader((tmp_path / "daily_revenue.csv").open()))
    assert len(rows) == 1  # no fake zero days/sources for incomplete acquisitions
    assert rows[0]["renewal_eth"] == "0.000000000000000100"
    assert rows[0]["total_revenue_eth"] == "0.000000000000000100"
    assert list(rows[0])[-1] == "total_revenue_eth"
    details = list(csv.DictReader((tmp_path / "daily_revenue_by_source.csv").open()))
    assert details[0]["renewal_overstatement_wei"] == "20"
    assert details[0]["coverage"] == "partial_day"
    assert json.loads((tmp_path / "manifest.json").read_text())["status"] == "partial"
    with gzip.open(tmp_path / "events.csv.gz", "rt") as stream:
        assert list(csv.DictReader(stream))[0]["revenue_wei"] == "100"
    before = (tmp_path / "daily_revenue.csv").read_bytes()
    p.export(info)
    assert (tmp_path / "daily_revenue.csv").read_bytes() == before


def test_daily_total_adds_base_premium_legacy_and_renewal_once(tmp_path):
    p = Pipeline(tmp_path, "unused")
    renewal_row = account_transaction([event()], 1_700_000_000, renewal([]))[0]
    split = dict(renewal_row, log_index=2, kind="registration", base_wei=10**18 + 1,
                 premium_wei=2 * 10**18 + 2, revenue_wei=3 * 10**18 + 3,
                 reported_cost_wei=3 * 10**18 + 3, correction_wei=0)
    legacy = dict(split, log_index=3, base_wei=None, premium_wei=None,
                  revenue_wei=4 * 10**18 + 4, reported_cost_wei=4 * 10**18 + 4)
    for row in [renewal_row, split, legacy]:
        p.db.execute("INSERT INTO events VALUES (?,?,?,?)", (row["transaction_hash"], row["log_index"], row["block_number"], json.dumps(row)))
    p.db.execute("INSERT INTO chunks VALUES (?,?)", (17_000_000, 17_000_001))
    p.export(dict(start_block=17_000_000, end_block=17_000_000,
                  start_timestamp=1_700_000_000, end_timestamp=1_700_000_100))
    rows = list(csv.DictReader((tmp_path / "daily_revenue.csv").open()))
    assert rows == [{"date": "2023-11-14", "registration_base_eth": "1.000000000000000001",
                     "registration_premium_eth": "2.000000000000000002",
                     "registration_combined_legacy_eth": "4.000000000000000004",
                     "renewal_eth": "0.000000000000000100", "total_revenue_eth": "7.000000000000000107"}]


def test_compressed_journal_preserves_arbitrary_integer_precision():
    event = {"revenue_wei": 10**100 + 1, "name": "猫.eth"}
    raw = json.dumps(event)
    assert load_event(raw) == event
    assert load_event(b"Z1" + zlib.compress(raw.encode())) == event
