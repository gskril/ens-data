import Papa from 'papaparse';

export type Row = { date: string; [key: string]: string | number | null };
export type Interval = 'day' | 'week' | 'month';
export const revenueKeys = ['registration_base_eth', 'registration_premium_eth', 'registration_combined_legacy_eth', 'renewal_eth', 'total_revenue_eth', 'total_revenue_usd'];
export const activityKeys = ['registration_count', 'registration_duration_seconds', 'renewal_count', 'renewal_duration_seconds'];

export function parseCsv(csv: string, keys: string[]): Row[] {
  const parsed = Papa.parse<Record<string, string>>(csv, { header: true, skipEmptyLines: true });
  if (parsed.errors.length || !['date', ...keys].every(key => parsed.meta.fields?.includes(key))) throw new Error('The CSV has an unexpected format.');
  const seen = new Set<string>();
  return parsed.data.map(raw => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(raw.date) || !Number.isFinite(Date.parse(raw.date)) || new Date(raw.date).toISOString().slice(0, 10) !== raw.date || seen.has(raw.date)) throw new Error('The CSV contains an invalid or duplicate date.');
    seen.add(raw.date);
    const row: Row = { date: raw.date };
    for (const key of keys) {
      const value = raw[key].trim() === '' ? null : Number(raw[key]);
      if (value !== null && (!Number.isFinite(value) || value < 0)) throw new Error(`Invalid value in ${key}.`);
      row[key] = value;
    }
    return row;
  }).sort((a, b) => a.date.localeCompare(b.date));
}

export function bucket(date: string, interval: Interval): string {
  if (interval === 'month') return `${date.slice(0, 7)}-01`;
  if (interval === 'week') {
    const day = new Date(`${date}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - (day.getUTCDay() + 6) % 7);
    return day.toISOString().slice(0, 10);
  }
  return date;
}

// A missing daily value makes its whole bucket unknown, never a misleading subtotal.
export function aggregate(rows: Row[], keys: string[], start: string, end: string, interval: Interval): Row[] {
  const groups = new Map<string, Row>();
  for (const row of rows) {
    if (row.date < start || row.date > end) continue;
    const date = bucket(row.date, interval);
    const group = groups.get(date) ?? { date, ...Object.fromEntries(keys.map(key => [key, 0])) };
    for (const key of keys) {
      group[key] = group[key] === null || row[key] == null ? null : Number(group[key]) + Number(row[key]);
    }
    groups.set(date, group);
  }
  return [...groups.values()];
}

export function total(rows: Row[], key: string): number {
  return rows.reduce((sum, row) => sum + (typeof row[key] === 'number' ? row[key] : 0), 0);
}
