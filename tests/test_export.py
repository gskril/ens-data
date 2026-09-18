import csv
import json
import gzip
import zlib

from ens_data.pipeline import Pipeline, load_event, file_sha256, split_large_csv
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
    assert list(rows[0])[-1] == "total_revenue_usd"
    assert rows[0]["total_revenue_usd"] == ""
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
                     "renewal_eth": "0.000000000000000100", "total_revenue_eth": "7.000000000000000107",
                     "total_revenue_usd": ""}]
    path = tmp_path / "name_events" / "2023.csv"
    with path.open(encoding="utf-8", newline="") as stream:
        simple = list(csv.DictReader(stream))
    assert simple == [
        {"name": row["name"] + ".eth", "event": kind, "price_eth": price, "date": "2023-11-14"}
        for row, kind, price in [
            (renewal_row, "renew", "0.000000000000000100"),
            (split, "register", "3.000000000000000003"),
            (legacy, "register", "4.000000000000000004"),
        ]
    ]
    assert list(simple[0]) == ["name", "event", "price_eth", "date"]
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["files_sha256"]["name_events/2023.csv"] == file_sha256(path)
    before = path.read_bytes()
    p.export(dict(start_block=17_000_000, end_block=17_000_000,
                  start_timestamp=1_700_000_000, end_timestamp=1_700_000_100))
    assert path.read_bytes() == before


def test_compressed_journal_preserves_arbitrary_integer_precision():
    event = {"revenue_wei": 10**100 + 1, "name": "猫.eth"}
    raw = json.dumps(event)
    assert load_event(raw) == event
    assert load_event(b"Z1" + zlib.compress(raw.encode())) == event


def test_name_events_split_years_and_refresh_existing_exports(tmp_path):
    p = Pipeline(tmp_path, "unused")
    first = account_transaction([event()], 1_700_000_000, renewal([]))[0]
    first["name"] = '猫,"example'
    info = dict(start_block=17_000_000, end_block=17_000_002,
                start_timestamp=1_700_000_000, end_timestamp=1_735_689_600)
    def insert(row):
        p.db.execute("INSERT INTO events VALUES (?,?,?,?)",
                     (row["transaction_hash"], row["log_index"], row["block_number"], json.dumps(row)))
    insert(first)
    p.export(info)
    second = dict(first, log_index=2, date="2024-01-01")
    third = dict(first, log_index=3, date="2023-12-31")
    insert(second)
    insert(third)
    stale = tmp_path / "name_events" / "2000.csv"
    stale.write_text("stale")
    p.export(info)
    root = tmp_path / "name_events"
    assert sorted(path.name for path in root.iterdir()) == ["2023.csv", "2024.csv"]
    for year, expected in [("2023", [first, third]), ("2024", [second])]:
        with (root / f"{year}.csv").open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
        assert [row["date"] for row in rows] == [row["date"] for row in expected]
        assert all(row["name"] == '猫,"example.eth' for row in rows)


def test_oversized_year_parts_preserve_csv_records(tmp_path):
    path = tmp_path / "2022.csv"
    rows = [["name", "event", "price_eth", "date"]] + [
        ['猫,"line\nbreak.eth', "register", "1.000000000000000001", "2022-01-01"]
    ] * 5
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows(rows)
    parts = split_large_csv(path, limit=180)
    assert len(parts) > 1
    assert not path.exists()
    restored = []
    for part in parts:
        assert part.stat().st_size <= 180
        with part.open(encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            assert next(reader) == rows[0]
            restored.extend(reader)
    assert restored == rows[1:]
    assert split_large_csv(parts[0], limit=180) == [parts[0]]
