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
    assert rows[0]["revenue_wei"] == "100"
    assert rows[0]["renewal_overstatement_wei"] == "20"
    assert rows[0]["coverage"] == "partial_day"
    assert json.loads((tmp_path / "manifest.json").read_text())["status"] == "partial"
    with gzip.open(tmp_path / "events.csv.gz", "rt") as stream:
        assert list(csv.DictReader(stream))[0]["revenue_wei"] == "100"
    before = (tmp_path / "daily_revenue.csv").read_bytes()
    p.export(info)
    assert (tmp_path / "daily_revenue.csv").read_bytes() == before


def test_compressed_journal_preserves_arbitrary_integer_precision():
    event = {"revenue_wei": 10**100 + 1, "name": "猫.eth"}
    raw = json.dumps(event)
    assert load_event(raw) == event
    assert load_event(b"Z1" + zlib.compress(raw.encode())) == event
