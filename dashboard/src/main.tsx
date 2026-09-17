import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import Papa from 'papaparse';
import { activityKeys, aggregate, parseCsv, revenueKeys, total, type Interval, type Row } from './data';
import './style.css';

const repo = 'https://github.com/gskril/ens-data';
const views = {
  revenue: { label: 'Revenue', unit: 'ETH', keys: ['total_revenue_eth'], names: ['Total revenue'], source: 'daily_revenue.csv' },
  sources: { label: 'Revenue by source', unit: 'ETH', keys: revenueKeys.slice(0, 4), names: ['Registration base', 'Registration premium', 'Legacy registration (combined)', 'Renewals'], source: 'daily_revenue.csv' },
  usd: { label: 'Revenue in USD', unit: 'USD', keys: ['total_revenue_usd'], names: ['Revenue in USD'], source: 'daily_revenue.csv' },
  activity: { label: 'Activity', unit: 'events', keys: ['registration_count', 'renewal_count'], names: ['Registrations', 'Renewals'], source: 'daily_activity.csv' },
  duration: { label: 'Purchased duration', unit: 'years', keys: ['registration_years', 'renewal_years'], names: ['Registration years', 'Renewal years'], source: 'daily_activity.csv' },
};
type View = keyof typeof views;
const colors = ['#3478ec', '#23a88a', '#a082cf', '#e6a345'];
const compact = (value: number) => new Intl.NumberFormat('en', { notation: 'compact', maximumFractionDigits: 1 }).format(value);
const number = (value: number) => new Intl.NumberFormat('en', { maximumFractionDigits: 2 }).format(value);

function App() {
  const [data, setData] = useState<Row[]>([]);
  const [error, setError] = useState('');
  const [view, setView] = useState<View>('revenue');
  const [interval, setInterval] = useState<Interval>('month');
  const [chart, setChart] = useState('area');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [hidden, setHidden] = useState<string[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    async function load() {
      try {
        const [revenue, activity] = await Promise.all([
          ['daily_revenue.csv', revenueKeys], ['daily_activity.csv', activityKeys],
        ].map(async ([file, keys]) => {
          const response = await fetch(`${import.meta.env.BASE_URL}data/${file}`, { signal: controller.signal });
          if (!response.ok) throw new Error(`Could not load ${file} (${response.status}).`);
          return parseCsv(await response.text(), keys as string[]);
        }));
        const byDate = new Map(activity.map(row => [row.date, row]));
        if (!revenue.length || revenue.length !== activity.length || revenue.some(row => !byDate.has(row.date))) throw new Error('Revenue and activity dates do not match.');
        const combined = revenue.map(row => {
          const activityRow = byDate.get(row.date)!;
          return { ...row, ...activityRow,
            registration_years: activityRow.registration_duration_seconds === null ? null : Number(activityRow.registration_duration_seconds) / 31557600,
            renewal_years: activityRow.renewal_duration_seconds === null ? null : Number(activityRow.renewal_duration_seconds) / 31557600 };
        });
        setData(combined);
        setStart(combined[0].date);
        setEnd(combined.at(-1)!.date);
      } catch (err) { if (!controller.signal.aborted) setError(err instanceof Error ? err.message : 'Unable to load the data.'); }
    }
    void load();
    return () => controller.abort();
  }, []);
  const config = views[view];
  const filtered = useMemo(() => data.filter(row => row.date >= start && row.date <= end), [data, start, end]);
  const points = useMemo(() => aggregate(data, config.keys, start, end, interval), [data, config, start, end, interval]);
  const activeKeys = config.keys.filter(key => !hidden.includes(key));
  const missingUsd = filtered.filter(row => row.total_revenue_usd === null).length;
  const invalid = !start || !end || start > end;
  function preset(days?: number) {
    const latest = data.at(-1)!.date;
    const first = new Date(`${latest}T00:00:00Z`);
    if (days) first.setUTCDate(first.getUTCDate() - days + 1);
    setStart(days ? [data[0].date, first.toISOString().slice(0, 10)].sort().at(-1)! : data[0].date);
    setEnd(latest);
  }
  function download() {
    const csv = Papa.unparse(points.map(row => Object.fromEntries(['date', ...activeKeys].map(key => [key, row[key]]))));
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8;' }));
    const link = document.createElement('a');
    link.href = url; link.download = `ens-${view}-${interval}-${start}-${end}.csv`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  return <main>
    <header><a className="brand" href={repo}><span className="mark">≋</span> ENS <span className="brand-divider">/</span> <span className="brand-sub">Data explorer</span></a><a className="source-link" href={repo}>View source ↗</a></header>
    <section className="intro"><p className="eyebrow">PUBLIC DATA · ETHEREUM MAINNET</p><h1>A clearer view of ENS.</h1><p className="subtitle">Explore registration revenue and activity over time.<br />Open data, straight from the daily CSVs.</p></section>
    {error ? <section className="state" role="alert"><h2>Unable to load the datasets</h2><p>{error}</p><button onClick={() => location.reload()}>Try again</button></section> : !data.length ? <section className="state" role="status">Loading daily datasets…</section> : <>
      <div className="snapshot"><span className="dot" /> Snapshot through {data.at(-1)!.date} <span>· All dates UTC</span></div>
      <section className="explorer" aria-label="Chart explorer">
        <div className="controls">
          <label>Explore<select aria-label="Explore" value={view} onChange={e => { setView(e.target.value as View); setHidden([]); }}>{Object.entries(views).map(([key, value]) => <option key={key} value={key}>{value.label}</option>)}</select></label>
          <label>Group by<select aria-label="Group by" value={interval} onChange={e => setInterval(e.target.value as Interval)}><option value="day">Day</option><option value="week">Week</option><option value="month">Month</option></select></label>
          <label>Chart style<select aria-label="Chart style" value={chart} onChange={e => setChart(e.target.value)}><option value="area">Area</option><option value="line">Line</option><option value="bar">Bar</option></select></label>
          <div className="date-controls"><label>From<input aria-label="From date" type="date" min={data[0].date} max={data.at(-1)!.date} value={start} onChange={e => setStart(e.target.value)} /></label><span className="date-dash">—</span><label>To<input aria-label="To date" type="date" min={data[0].date} max={data.at(-1)!.date} value={end} onChange={e => setEnd(e.target.value)} /></label></div>
        </div>
        <div className="range-row"><span>{filtered.length.toLocaleString()} daily observations</span><div className="presets"><span>Range</span><button onClick={() => preset(90)}>90D</button><button onClick={() => preset(365)}>1Y</button><button onClick={() => preset(365 * 3)}>3Y</button><button onClick={() => preset()}>All time</button></div></div>
        <div className="stats"><div><span>Revenue · ETH</span><strong>{number(total(filtered, 'total_revenue_eth'))}</strong><small>Gross controller fees</small></div><div><span>Revenue · USD</span><strong>${compact(total(filtered, 'total_revenue_usd'))}</strong><small>{missingUsd ? `Priced dates only · ${missingUsd} missing` : 'At daily closing ETH/USD prices'}</small></div><div><span>Registrations</span><strong>{number(total(filtered, 'registration_count'))}</strong><small>Unique registration events</small></div><div><span>Renewals</span><strong>{number(total(filtered, 'renewal_count'))}</strong><small>Unique renewal events</small></div></div>
        <div className="chart-heading"><div><h2>{config.label}</h2><p>{interval === 'day' ? 'Daily' : interval === 'week' ? 'Weekly' : 'Monthly'} totals · {config.unit}{activeKeys.length > 1 && chart !== 'line' ? ' · stacked' : ''}</p></div><button className="export" disabled={invalid || !points.length || !activeKeys.length} onClick={download}>Export view ↓</button></div>
        {config.keys.length > 1 && <div className="series" aria-label="Visible series">{config.keys.map((key, index) => <label key={key}><input type="checkbox" checked={!hidden.includes(key)} onChange={() => setHidden(previous => previous.includes(key) ? previous.filter(k => k !== key) : [...previous, key])} /><span style={{ background: colors[index] }} />{config.names[index]}</label>)}</div>}
        {invalid ? <div className="state" role="alert">Choose a valid date range with the start on or before the end.</div> : !points.length ? <div className="state" role="status">No observations in this date range. Try a wider range.</div> : !activeKeys.length ? <div className="state" role="status">Select at least one series to display.</div> : <div className="chart" role="img" aria-label={`${config.label}, ${interval} totals from ${start} to ${end}. Data is available in the table below.`}><ResponsiveContainer width="100%" height="100%"><ComposedChart data={points} margin={{ top: 12, right: 14, bottom: 8, left: 5 }} accessibilityLayer>
          <CartesianGrid stroke="#edf0f3" vertical={false} /><XAxis dataKey="date" minTickGap={55} tickFormatter={value => new Date(`${value}T00:00:00Z`).toLocaleDateString('en', { month: 'short', year: '2-digit', timeZone: 'UTC' })} tickLine={false} axisLine={false} tick={{ fill: '#7a8493', fontSize: 12 }} dy={10} /><YAxis tickFormatter={compact} width={62} tickLine={false} axisLine={false} tick={{ fill: '#7a8493', fontSize: 12 }} />
          <Tooltip labelFormatter={label => `${interval === 'day' ? 'Date' : 'Period starting'} ${label} (UTC)`} formatter={(value) => [value == null ? 'Unavailable' : `${number(Number(value))} ${config.unit}`]} contentStyle={{ borderRadius: 10, border: '1px solid #e5e9ed', fontSize: 13 }} />
          <Legend wrapperStyle={{ fontSize: 12, paddingTop: 20 }} />
          {config.keys.map((key, index) => hidden.includes(key) ? null : chart === 'line' ? <Line key={key} dataKey={key} name={config.names[index]} stroke={colors[index]} strokeWidth={2} dot={false} isAnimationActive={false} connectNulls={false} /> : chart === 'bar' ? <Bar key={key} dataKey={key} name={config.names[index]} stackId="total" fill={colors[index]} isAnimationActive={false} /> : <Area key={key} dataKey={key} name={config.names[index]} stackId="total" stroke={colors[index]} fill={colors[index]} fillOpacity={0.15} strokeWidth={2} isAnimationActive={false} connectNulls={false} />)}
        </ComposedChart></ResponsiveContainer></div>}
        <p className="chart-note">{view === 'usd' && missingUsd > 0 ? `${missingUsd} dates have no usable USD price. Any period containing a missing price is shown as a gap. ` : ''}Weeks begin Monday. Edge periods include only selected dates; snapshot boundary days may be partial.</p>
      </section>
      <details className="table-panel"><summary>View chart data <span>{points.length} periods</span></summary><div className="table-scroll"><table><caption>{config.label} · {config.unit} · {interval} totals</caption><thead><tr><th scope="col">Period starting (UTC)</th>{activeKeys.map(key => <th scope="col" key={key}>{config.names[config.keys.indexOf(key)]}</th>)}</tr></thead><tbody>{points.map(row => <tr key={row.date}><th scope="row">{row.date}</th>{activeKeys.map(key => <td key={key}>{row[key] === null ? 'Unavailable' : number(Number(row[key]))}</td>)}</tr>)}</tbody></table></div></details>
      <section className="notes"><div><h3>Understand the numbers</h3><p>Cash-basis gross fees from permanent-registrar .eth controllers. Legacy registration fees combine base rent and premium; their split is unavailable. Purchased years use 365.25 days. Chart values are rounded for display; the source CSVs retain exact ETH amounts.</p></div><div><h3>Take the data with you</h3><p>USD uses daily Chainlink closing prices, with historical gaps. This is a fixed snapshot, not a live feed. <a href={`${repo}#coverage-and-provenance`}>Read the methodology ↗</a></p><div className="downloads"><a href={`${import.meta.env.BASE_URL}data/daily_revenue.csv`} download>Revenue CSV ↓</a><a href={`${import.meta.env.BASE_URL}data/daily_activity.csv`} download>Activity CSV ↓</a></div></div></section>
    </>}
    <footer><span>ENS data explorer</span><span>Public datasets. Reproducible history.</span><a href={repo}>GitHub ↗</a></footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
