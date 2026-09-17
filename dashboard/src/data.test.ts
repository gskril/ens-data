import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { aggregate, bucket, parseCsv, revenueKeys, activityKeys, total } from './data';

test('date filtering is inclusive and happens before aggregation', () => {
  const rows = parseCsv('date,value\n2024-01-01,100\n2024-01-02,2\n2024-01-03,3\n2024-01-04,100', ['value']);
  assert.deepEqual(aggregate(rows, ['value'], '2024-01-02', '2024-01-03', 'month'), [{ date: '2024-01-01', value: 5 }]);
  assert.deepEqual(aggregate(rows, ['value'], '2025-01-01', '2025-02-01', 'day'), []);
});
test('missing prices remain gaps, including aggregated periods', () => {
  const rows = parseCsv('date,usd\r\n2024-01-01,0\r\n2024-01-02,\r\n2024-01-03,5\r\n', ['usd']);
  assert.equal(rows[0].usd, 0);
  assert.equal(rows[1].usd, null);
  assert.equal(aggregate(rows, ['usd'], '2024-01-01', '2024-01-31', 'month')[0].usd, null);
  assert.equal(total(rows, 'usd'), 5);
});
test('weeks start Monday in UTC across year boundaries', () => {
  assert.equal(bucket('2023-01-01', 'week'), '2022-12-26');
  assert.equal(bucket('2024-01-01', 'week'), '2024-01-01');
});
test('reject malformed input instead of rendering zeroes', () => {
  for (const csv of ['date,wrong\n2024-01-01,1', 'date,x\n2024-02-30,1', 'date,x\n2024-01-01,no', 'date,x\n2024-01-01,1\n2024-01-01,2']) assert.throws(() => parseCsv(csv, ['x']));
});
test('real CSVs reconcile across all granularities', () => {
  for (const [file, keys] of [['daily_revenue.csv', revenueKeys], ['daily_activity.csv', activityKeys]] as const) {
    const rows = parseCsv(readFileSync(new URL(`../../data/${file}`, import.meta.url), 'utf8'), keys);
    assert.ok(rows.length > 2000);
    for (const interval of ['day', 'week', 'month'] as const) {
      const grouped = aggregate(rows, keys, rows[0].date, rows.at(-1)!.date, interval);
      for (const key of keys.filter(key => !key.endsWith('_usd'))) assert.ok(Math.abs(total(rows, key) - total(grouped, key)) < Math.max(1e-8, total(rows, key) * 1e-12));
    }
    if (file === 'daily_revenue.csv') for (const row of rows) assert.ok(Math.abs(Number(row.total_revenue_eth) - revenueKeys.slice(0, 4).reduce((s, key) => s + Number(row[key]), 0)) < 1e-8);
  }
});
