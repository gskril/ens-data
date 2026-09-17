"""Reconcile the captured public dashboard figures with the local daily CSVs.

Run from any directory: python research/compare_dashboard.py
Source capture and definitions: dashboard-comparison.md.
"""
import csv
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    with (ROOT / 'research/dashboard-source.csv').open(newline='') as stream:
        source = list(csv.DictReader(stream))
    with (ROOT / 'data/daily_revenue.csv').open(newline='') as stream:
        local = list(csv.DictReader(stream))
    with (ROOT / 'data/daily_activity.csv').open(newline='') as stream:
        activity = {row['date']: row for row in csv.DictReader(stream)}
    assert len({(r['start_date'], r['end_date'], r['aggregation']) for r in source}) == len(source)
    assert len({r['date'] for r in local}) == len(local)
    comparisons = []
    for row in source:
        if not row['eth_revenue']:
            continue
        days = [r for r in local if row['start_date'] <= r['date'] <= row['end_date']]
        if not days:
            raise ValueError(f"No local data for {row['start_date']}–{row['end_date']}")
        if row['end_date'] > local[-1]['date']:
            raise ValueError('Dashboard extends past local data; align the cutoffs first')
        ours = sum(Decimal(r['total_revenue_eth']) for r in days)
        dashboard = Decimal(row['eth_revenue'])
        difference = ours - dashboard
        percent = difference / dashboard * 100 if dashboard else None
        missing = sum(bool(Decimal(r['total_revenue_eth'])) and not r['total_revenue_usd'] for r in days)
        usd = sum(Decimal(r['total_revenue_usd']) for r in days if r['total_revenue_usd'])
        comparisons.append(dict(
            start_date=row['start_date'], end_date=row['end_date'], aggregation=row['aggregation'],
            dashboard_revenue_eth=str(dashboard), indexed_revenue_eth=str(ours),
            indexed_minus_dashboard_eth=str(difference),
            eth_difference_percent=str(percent.quantize(Decimal('.00000001'))) if percent is not None else '',
            within_half_percent=str(abs(percent) <= Decimal('.5')).lower() if percent is not None else str(difference == 0).lower(),
            dashboard_revenue_usd=row['usd_revenue'], indexed_revenue_usd=str(usd) if not missing else '',
            indexed_unpriced_revenue_days=missing,
            dashboard_registrations=row['registration_count'],
            indexed_registrations=sum(int(activity[r['date']]['registration_count']) for r in days),
            dashboard_renewals=row['renewal_count'],
            indexed_renewals=sum(int(activity[r['date']]['renewal_count']) for r in days)))
    target = ROOT / 'research/dashboard-comparison.csv'
    with target.open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(comparisons[0]))
        writer.writeheader()
        writer.writerows(comparisons)
    print(f'Wrote {len(comparisons)} comparisons to {target}')


if __name__ == '__main__':
    main()
