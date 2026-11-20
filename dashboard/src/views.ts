import { revenueKeys } from './data';

export type ViewConfig = { label: string; unit: string; keys: string[]; names: string[]; source: string };

// Order is the Explore menu order; the first view is the default. Activity, duration and USD
// revenue span ENSv1 and ENSv2. ETH revenue only exists for ENSv1, which stopped on 2026-10-27.
export const views = {
  activity: { label: 'Registrations and renewals (count)', unit: 'events', keys: ['registration_count', 'renewal_count'], names: ['Registrations', 'Renewals'], source: 'daily_activity.csv' },
  duration: { label: 'Purchased duration (years)', unit: 'years', keys: ['registration_years', 'renewal_years'], names: ['Registration years', 'Renewal years'], source: 'daily_activity.csv' },
  usd: { label: 'Revenue in USD (ENSv1 + ENSv2)', unit: 'USD', keys: ['total_revenue_usd'], names: ['Total revenue in USD'], source: 'daily_revenue.csv' },
  v2: { label: 'ENSv2 revenue by source (USD)', unit: 'USD', keys: revenueKeys.slice(5, 8), names: ['Registration base', 'Registration premium', 'Renewals'], source: 'daily_revenue.csv' },
  revenue: { label: 'ENSv1 revenue (ETH)', unit: 'ETH', keys: ['total_revenue_eth'], names: ['ENSv1 revenue in ETH'], source: 'daily_revenue.csv' },
  sources: { label: 'ENSv1 revenue by source (ETH)', unit: 'ETH', keys: revenueKeys.slice(0, 4), names: ['Registration base', 'Registration premium', 'Legacy registration (combined)', 'Renewals'], source: 'daily_revenue.csv' },
} satisfies Record<string, ViewConfig>;
export type View = keyof typeof views;
export const defaultView = Object.keys(views)[0] as View;
