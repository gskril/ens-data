import { copyFile, mkdir } from 'node:fs/promises';

// Explicit allowlist: never publish the journal, raw evidence, or local environment.
const destination = new URL('../public/data/', import.meta.url);
await mkdir(destination, { recursive: true });
for (const name of ['daily_revenue.csv', 'daily_activity.csv']) {
  await copyFile(new URL(`../../data/${name}`, import.meta.url), new URL(name, destination));
}
