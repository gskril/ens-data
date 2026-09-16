from datetime import datetime, timezone

from ens_data.daily_prices import closing_rows, usd_amount


def timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def record(time, answer, phase=1, updated=None, block=1):
    return dict(available_at=timestamp(time), updated_at=timestamp(updated or time), answer=answer,
                phase_id=phase, aggregator="0xfeed", block_number=block, transaction_hash="0xtx", log_index=1)


def test_utc_close_excludes_midnight_update_and_caps_unfinished_day():
    records = [record("2024-01-01T23:59:59", 2000 * 10**8),
               record("2024-01-02T00:00:00", 2100 * 10**8, block=2),
               record("2024-01-02T18:00:00", 2200 * 10**8, block=3)]
    rows = list(closing_rows(["2023-12-31", "2024-01-01", "2024-01-02"], records, timestamp("2024-01-02T12:00:00")))
    assert rows[0]["status"] == "unavailable"
    assert rows[1]["answer"] == 2000 * 10**8 and rows[1]["status"] == "close"
    assert rows[2]["answer"] == 2100 * 10**8 and rows[2]["status"] == "snapshot_cutoff"


def test_phase_activation_uses_seed_without_backdating_the_new_feed():
    records = [record("2024-01-01T23:00:00", 2000 * 10**8),
               record("2024-01-02T12:00:00", 2100 * 10**8, phase=2,
                      updated="2024-01-01T23:30:00", block=2)]
    rows = list(closing_rows(["2024-01-01", "2024-01-02"], records, timestamp("2024-01-02T23:59:59")))
    assert rows[0]["phase_id"] == 1
    assert rows[1]["phase_id"] == 2
    assert rows[1]["status"] == "stale"  # A day-old quote is not silently used as a closing price.
    assert usd_amount(10**18, rows[1]) == ""


def test_usd_rounding_is_exact_and_missing_prices_are_not_zero_revenue():
    quote = dict(answer=200050000000, decimals=8, status="close")
    assert usd_amount(10**18, quote) == "2000.50"
    assert usd_amount(10**16, quote) == "20.01"  # $20.005 rounds half up.
    assert usd_amount(10**16 - 1, quote) == "20.00"
    assert usd_amount(10**18, None) == ""
    assert usd_amount(0, None) == "0.00"


def test_export_keeps_usd_as_last_column_and_reuses_saved_prices(tmp_path):
    import csv
    from ens_data.pipeline import write_daily_revenue

    (tmp_path / "daily_revenue_by_source.csv").write_text(
        "date,source,revenue_wei\n2024-01-01,registration_base,1000000000000000000\n"
        "2024-01-01,renewal,2000000000000000000\n")
    (tmp_path / "daily_eth_usd.csv").write_text(
        "date,answer,decimals,status\n2024-01-01,200050000000,8,close\n")
    write_daily_revenue(tmp_path)
    with (tmp_path / "daily_revenue.csv").open() as stream:
        row = next(csv.DictReader(stream))
    assert list(row)[-2:] == ["total_revenue_eth", "total_revenue_usd"]
    assert row["total_revenue_eth"] == "3.000000000000000000"
    assert row["total_revenue_usd"] == "6001.50"
