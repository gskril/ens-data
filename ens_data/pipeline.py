"""Resumable Cryo acquisition, disk-backed journal, and auditable CSV export."""
import csv
import gzip
import hashlib
import io
import json
import sqlite3
import time
import zlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import cryo
import polars as pl
import requests

from .accounting import account_transaction, decode_log, eth, revenue_parts, raw_bytes, hexstr, duration_key, match_durations
from .contracts import (CONTROLLERS, SOURCES, BY_ADDRESS, BASE_REGISTRARS,
                        BASE_REGISTERED, BASE_MIGRATED, BASE_RENEWED)
from .oracle import OracleHistory, solidity_length


def atomic_json(path, obj):
    temporary = Path(str(path) + ".tmp")
    temporary.write_text(json.dumps(obj, indent=2) + "\n")
    temporary.replace(path)


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_daily_revenue(root):
    """Pivot source-level accounting into one exact ETH revenue row per day."""
    from .daily_prices import read_prices, usd_amount
    root = Path(root)
    prices = read_prices(root)
    days = defaultdict(dict)
    with (root / "daily_revenue_by_source.csv").open(newline="") as stream:
        for row in csv.DictReader(stream):
            day, source = row["date"], row["source"]
            if source not in SOURCES or source in days[day]:
                raise ValueError("Unknown or duplicate daily revenue source")
            days[day][source] = int(row["revenue_wei"])
    temporary = root / "daily_revenue.csv.tmp"
    with temporary.open("w", newline="") as stream:
        columns = ["date", *[source + "_eth" for source in SOURCES], "total_revenue_eth", "total_revenue_usd"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for day, amounts in sorted(days.items()):
            writer.writerow({"date": day,
                             **{source + "_eth": eth(amounts.get(source, 0)) for source in SOURCES},
                             "total_revenue_eth": eth(sum(amounts.values())),
                             "total_revenue_usd": usd_amount(sum(amounts.values()), prices.get(day))})
    temporary.replace(root / "daily_revenue.csv")


def remove_merged_parts(path):
    if "__" not in path.stem:
        for child in path.parent.glob(path.stem + "__*.parquet"):
            child.unlink(missing_ok=True)


def load_event(payload):
    if isinstance(payload, bytes) and payload.startswith(b"Z1"):
        payload = zlib.decompress(payload[2:])
    return json.loads(payload)


class RPC:
    def __init__(self, url, rps):
        self.url, self.interval, self.last = url, 1 / rps, 0.0
        self.session = requests.Session()

    def call(self, method, params):
        for attempt in range(5):
            time.sleep(max(0, self.last + self.interval - time.monotonic()))
            self.last = time.monotonic()
            try:
                r = self.session.post(self.url, json=dict(jsonrpc="2.0", id=1, method=method, params=params), timeout=60)
                r.raise_for_status()
                body = r.json()
                if "error" in body or body.get("result") is None:
                    raise ValueError(f"{method}: {body.get('error', 'null result')}")
                return body["result"]
            except (requests.RequestException, ValueError):
                if attempt == 4:
                    # Do not include request URLs, which may contain credentials.
                    raise RuntimeError(f"RPC {method} failed after 5 attempts") from None
                time.sleep(min(2**attempt, 16))

    def block(self, number):
        return self.call("eth_getBlockByNumber", [hex(number) if isinstance(number, int) else number, False])


class Pipeline:
    def __init__(self, root, rpc_url, rps=1, concurrency=1, chunk_size=10_000, log_request_size=20_000, trace_only=False):
        self.root = Path(root)
        self.raw = self.root / "raw"
        self.raw.mkdir(parents=True, exist_ok=True)
        self.rpc = RPC(rpc_url, rps)
        self.rps, self.concurrency, self.chunk_size = rps, concurrency, chunk_size
        self.log_request_size = log_request_size
        self.trace_only = trace_only
        self.alchemy_metadata = (urlparse(rpc_url or "").hostname or "").endswith(".alchemy.com")
        self.db = sqlite3.connect(self.root / "journal.sqlite")
        self.db.executescript("""
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS chunks (start INTEGER PRIMARY KEY, stop INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS expiries (registrar TEXT, labelhash TEXT, expiry TEXT,
            PRIMARY KEY(registrar,labelhash));
          CREATE TABLE IF NOT EXISTS expiry_chunks (start INTEGER PRIMARY KEY, stop INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS events (
            transaction_hash TEXT, log_index INTEGER, block_number INTEGER, payload TEXT NOT NULL,
            PRIMARY KEY(transaction_hash, log_index));
          CREATE INDEX IF NOT EXISTS event_order ON events(block_number,log_index);
        """)

    def collect(self, dataset, key, **kwargs):
        actual_dataset = kwargs.pop("_dataset", dataset)
        request_rate = kwargs.pop("_rps", self.rps)
        request_concurrency = kwargs.pop("_concurrency", self.concurrency)
        path = self.raw / dataset / f"{key}.parquet"
        if path.exists():
            return pl.read_parquet(path)
        path.parent.mkdir(exist_ok=True)
        ranges = kwargs.get("blocks", [])
        request_size = kwargs.get("inner_request_size", self.log_request_size)
        # Cryo 0.3.2 collect() can send a full range despite inner_request_size.
        # Enforce the configured wire-level limit ourselves and retain its raw
        # Parquet output, rather than relying on a newer Cryo CLI's behavior.
        if actual_dataset == "logs" and len(ranges) == 1 and ":" in ranges[0]:
            first, stop = map(int, ranges[0].split(":"))
            if stop - first > request_size:
                workers = min(4, self.concurrency, max(1, request_rate))
                def extract_part(left):
                    right = min(left + request_size, stop)
                    options = dict(kwargs, blocks=[f"{left}:{right}"], _dataset=actual_dataset,
                                   _rps=max(1, request_rate // workers), _concurrency=1)
                    subkey = key.split("__")[0] + f"__{left}-{right}"
                    return self.collect(dataset, subkey, **options)
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    frames = list(executor.map(extract_part, range(first, stop, request_size)))
                frame = pl.concat(frames, how="vertical_relaxed")
                temporary = path.with_suffix(".tmp")
                frame.write_parquet(temporary)
                temporary.replace(path)
                remove_merged_parts(path)
                return frame
        for attempt in range(4):
            try:
                # Explicit output format/columns avoid uint256 float conversions.
                frame = cryo.collect(actual_dataset, rpc=self.rpc.url, hex=True, verbose=False,
                                     requests_per_second=request_rate, max_concurrent_requests=request_concurrency,
                                     **kwargs)
                break
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                ranges = kwargs.get("blocks", [])
                if attempt >= 1 and actual_dataset == "logs" and len(ranges) == 1 and ":" in ranges[0]:
                    first, stop = map(int, ranges[0].split(":"))
                    if stop - first > 1:
                        middle = (first + stop) // 2
                        print(f"Splitting {dataset} range {first:,}–{stop - 1:,} after provider failure", flush=True)
                        frames = []
                        for left, right in [(first, middle), (middle, stop)]:
                            options = dict(kwargs, blocks=[f"{left}:{right}"], _dataset=actual_dataset,
                                           _rps=request_rate, _concurrency=request_concurrency)
                            subkey = key.split("__")[0] + f"__{left}-{right}"
                            frames.append(self.collect(dataset, subkey, **options))
                        frame = pl.concat(frames, how="vertical_relaxed")
                        break
                if attempt == 3:
                    raise RuntimeError(f"Cryo {dataset}/{key} failed; rerun to resume") from None
                time.sleep(2**attempt)
        if "chain_id" in frame.columns and any(x != 1 for x in frame["chain_id"].to_list()):
            raise ValueError("Cryo returned a non-mainnet row")
        temp = path.with_suffix(".tmp")
        frame.write_parquet(temp)
        temp.replace(path)
        remove_merged_parts(path)
        return frame

    def initialize(self, start, end):
        if int(self.rpc.call("eth_chainId", []), 16) != 1:
            raise ValueError("Expected Ethereum mainnet (chain ID 1)")
        saved = self.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()
        if saved:
            info = json.loads(saved[0])
            if info["start_block"] != start or (end is not None and end != info["end_block"]):
                raise ValueError("This output directory belongs to a different range; use a new --output directory")
            if self.rpc.block(info["end_block"])["hash"] != info["end_block_hash"]:
                raise ValueError("Saved end-block hash changed; use a fresh output directory")
            self.chunk_size = info["chunk_size"]
            return info
        finalized = self.rpc.block("finalized")
        final_number = int(finalized["number"], 16)
        if end is None:
            end = final_number
        if not 0 <= start <= end <= final_number:
            raise ValueError("Require 0 <= start <= end <= finalized block")
        first = self.rpc.block(start)
        last = finalized if end == final_number else self.rpc.block(end)
        info = dict(chain_id=1, start_block=start, end_block=end, end_block_hash=last["hash"],
                    start_timestamp=int(first["timestamp"], 16), end_timestamp=int(last["timestamp"], 16),
                    created_at=datetime.now(timezone.utc).isoformat(), cryo_version=cryo.__version__,
                    controllers=[c.__dict__ for c in CONTROLLERS], chunk_size=self.chunk_size)
        info["timestamp_source"] = "Alchemy transfer metadata with Cryo block fallback" if self.alchemy_metadata else "Cryo blocks"
        self.db.execute("INSERT INTO meta VALUES ('run',?)", (json.dumps(info),))
        self.db.commit()
        return info

    def transfer_timestamps(self, start, stop, events):
        """Bulk timestamp metadata avoids fetching millions of individual headers.

        Fees still come exclusively from Cryo logs/traces. Missing metadata falls
        back to actual headers; floating-point transfer values are never used.
        """
        if not self.alchemy_metadata or not events:
            return {}
        path = self.raw / "timestamps" / f"{start}-{stop}.json"
        if path.exists():
            return {int(k): v for k, v in json.loads(path.read_text()).items()}
        workers = min(4, self.concurrency, self.rps)
        span = max(1, (stop - start + workers - 1) // workers)
        groups = [(address, left, min(left + span, stop))
                  for address in sorted({e["controller"] for e in events})
                  for left in range(start, stop, span)
                  if any(e["controller"] == address and left <= e["block_number"] < min(left + span, stop) for e in events)]
        def fetch_group(group):
            address, left, right = group
            cached = self.raw / "timestamps" / f"{address}-{left}-{right}.json"
            if cached.exists():
                return {int(k): v for k, v in json.loads(cached.read_text()).items()}
            rpc = RPC(self.rpc.url, max(1, self.rps // workers))
            timestamps = {}
            params = dict(fromBlock=hex(left), toBlock=hex(right - 1), toAddress=address,
                          category=["external", "internal"], withMetadata=True,
                          excludeZeroValue=True, maxCount="0x3e8", order="asc")
            page_keys = set()
            while True:
                response = rpc.call("alchemy_getAssetTransfers", [params])
                for transfer in response["transfers"]:
                    stamp = transfer.get("metadata", {}).get("blockTimestamp")
                    if stamp:
                        number = int(transfer["blockNum"], 16)
                        if not left <= number < right:
                            raise ValueError("Transfer timestamp outside requested range")
                        timestamp = int(datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())
                        if number in timestamps and timestamps[number] != timestamp:
                            raise ValueError("Conflicting block timestamp metadata")
                        timestamps[number] = timestamp
                if not response.get("pageKey"):
                    break
                if response["pageKey"] in page_keys:
                    raise ValueError("Provider repeated a transfer pagination cursor")
                page_keys.add(response["pageKey"])
                params["pageKey"] = response["pageKey"]
            cached.parent.mkdir(exist_ok=True)
            atomic_json(cached, timestamps)
            return timestamps
        timestamps = {}
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for group_timestamps in executor.map(fetch_group, groups):
                for number, timestamp in group_timestamps.items():
                    if number in timestamps and timestamps[number] != timestamp:
                        raise ValueError("Conflicting block timestamp metadata")
                    timestamps[number] = timestamp
        path.parent.mkdir(exist_ok=True)
        atomic_json(path, timestamps)
        return timestamps

    def expiry_history(self, start, stop):
        """Replay both base registrars, including migrations and non-fee controllers.

        Expiry state is committed atomically with revenue events. This is also
        sufficient for renewals performed within the same transaction/block.
        """
        updates, durations = {}, {}
        rows = []
        for address, deployed in BASE_REGISTRARS:
            first = max(start, deployed)
            if first >= stop:
                continue
            frame = self.collect("base_logs", f"{address}-{first}-{stop}",
                                 _dataset="logs", blocks=[f"{first}:{stop}"], contract=[address],
                                 topic0=[BASE_REGISTERED, BASE_MIGRATED, BASE_RENEWED],
                                 inner_request_size=min(self.chunk_size, self.log_request_size))
            rows.extend(frame.iter_rows(named=True))
        for row in sorted(rows, key=lambda r: (r["block_number"], r["log_index"])):
            topic = hexstr(row["topic0"])
            if topic not in (BASE_REGISTERED, BASE_MIGRATED, BASE_RENEWED):
                continue
            key = (hexstr(row["address"]), hexstr(row["topic1"]))
            data = raw_bytes(row["data"])
            if len(data) != 32:
                raise ValueError("Unexpected base registrar expiry event ABI")
            expires = int.from_bytes(data, "big")
            if topic == BASE_RENEWED:
                old = updates.get(key)
                if old is None:
                    saved = self.db.execute("SELECT expiry FROM expiries WHERE registrar=? AND labelhash=?", key).fetchone()
                    old = int(saved[0]) if saved else None
                if old is not None:
                    duration = expires - old
                    if duration < 0:
                        raise ValueError("Base registrar renewal decreased expiry")
                    durations[(key[0], hexstr(row["transaction_hash"]), key[1], expires, row["log_index"])] = duration
            updates[key] = expires
        return updates, durations

    def commit_expiries(self, start, stop, updates):
        self.db.executemany("INSERT OR REPLACE INTO expiries VALUES (?,?,?)",
                            [(address, label, str(expiry)) for (address, label), expiry in updates.items()])
        self.db.execute("INSERT INTO expiry_chunks VALUES (?,?)", (start, stop))

    def process_chunk(self, start, stop):
        updates, history = self.expiry_history(start, stop)
        events = []
        for c in CONTROLLERS:
            first = max(start, c.start)
            if first >= stop:
                continue
            # Cryo block ranges are half-open, whereas JSON-RPC end blocks are inclusive.
            frame = self.collect("logs", f"{c.name}-{first}-{stop}", blocks=[f"{first}:{stop}"],
                                 contract=[c.address], inner_request_size=min(self.chunk_size, self.log_request_size))
            for row in frame.iter_rows(named=True):
                if not first <= row["block_number"] < stop:
                    raise ValueError("Log outside requested range")
                event = decode_log(row)
                if event:
                    events.append(event)
        durations = match_durations(events, history)
        by_tx = defaultdict(list)
        for event in events:
            by_tx[event["transaction_hash"]].append(event)
        timestamps = self.transfer_timestamps(start, stop, events)
        block_numbers = sorted({e["block_number"] for e in events} - timestamps.keys())
        # Small checkpoint batches preserve completed downloads after interruption.
        for offset in range(0, len(block_numbers), 100):
            numbers = block_numbers[offset:offset + 100]
            digest = hashlib.sha256(json.dumps(numbers).encode()).hexdigest()[:16]
            blocks = self.collect("blocks", digest, blocks=list(map(str, numbers)),
                                  columns=["block_number", "timestamp", "chain_id"])
            timestamps.update({r["block_number"]: r["timestamp"] for r in blocks.iter_rows(named=True)})
            if not set(numbers) <= timestamps.keys():
                raise ValueError("Cryo did not return all requested block timestamps")
        prices = {} if self.trace_only else OracleHistory(self).prices(start, stop, events, durations)
        transactions = set(tx for tx, ev in by_tx.items() if any(
            e["kind"] == "renewal" and ((BY_ADDRESS[e["controller"]].refund_bug and
                (tx, e["log_index"]) not in prices) or
                duration_key(e) not in durations) for e in ev))
        # Independently reconcile at least one event per price band per chunk
        # against execution traces, even when the full oracle series is present.
        sampled_bands = set()
        for event in sorted(events, key=lambda e: (e["block_number"], e["log_index"])):
            if (event["transaction_hash"], event["log_index"]) in prices:
                band = min(solidity_length(raw_bytes(event["name_bytes_hex"])), 5)
                if band not in sampled_bands:
                    sampled_bands.add(band)
                    transactions.add(event["transaction_hash"])
        transactions = sorted(transactions)
        traces = defaultdict(list)
        for offset in range(0, len(transactions), 50):
            hashes = transactions[offset:offset + 50]
            digest = hashlib.sha256(json.dumps(hashes).encode()).hexdigest()[:16]
            frame = self.collect("traces", digest, txs=hashes)
            for row in frame.iter_rows(named=True):
                traces[row["transaction_hash"]].append(row)
            if not set(hashes) <= traces.keys():
                raise ValueError("Cryo did not return all requested transaction traces")
        accounted = []
        for tx, ev in by_tx.items():
            accounted.extend(account_transaction(ev, timestamps[ev[0]["block_number"]], traces[tx], durations, prices))
        with self.db:
            for e in accounted:
                payload = b"Z1" + zlib.compress(json.dumps(e).encode("utf-8"), level=1)
                previous = self.db.execute("SELECT payload FROM events WHERE transaction_hash=? AND log_index=?",
                                           (e["transaction_hash"], e["log_index"])).fetchone()
                if previous and load_event(previous[0]) != e:
                    raise ValueError("Conflicting duplicate log")
                self.db.execute("INSERT OR IGNORE INTO events VALUES (?,?,?,?)",
                                (e["transaction_hash"], e["log_index"], e["block_number"], payload))
            self.db.execute("INSERT INTO chunks VALUES (?,?)", (start, stop))
            self.commit_expiries(start, stop, updates)
        return len(accounted)

    def run(self, start, end):
        info = self.initialize(start, end)
        error = None
        last_export = time.monotonic()
        try:
            # Resume with the original chunk boundaries even if CLI flags change.
            size = info["chunk_size"]
            for first in range(start, info["end_block"] + 1, size):
                if self.db.execute("SELECT 1 FROM chunks WHERE start=?", (first,)).fetchone():
                    # Upgrade existing journals by replaying only the missing base
                    # history, preserving already reconciled revenue and traces.
                    if not self.db.execute("SELECT 1 FROM expiry_chunks WHERE start=?", (first,)).fetchone():
                        stop = min(first + size, info["end_block"] + 1)
                        updates, _ = self.expiry_history(first, stop)
                        with self.db:
                            self.commit_expiries(first, stop, updates)
                    continue
                stop = min(first + size, info["end_block"] + 1)
                print(f"Extracting blocks {first:,}–{stop - 1:,}", flush=True)
                count = self.process_chunk(first, stop)
                self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                print(f"Committed {count:,} revenue events", flush=True)
                if time.monotonic() - last_export >= 1800:
                    self.export(info)
                    last_export = time.monotonic()
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self.export(info, error)

    def export(self, info=None, error=None):
        # A separate `export` command can run while acquisition commits chunks.
        # Keep coverage, event rows and totals on one consistent SQLite snapshot.
        if self.db.in_transaction:
            self.db.commit()
        self.db.execute("BEGIN")
        try:
            return self._export(info, error)
        finally:
            self.db.rollback()

    def _export(self, info=None, error=None):
        if info is None:
            saved = self.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()
            if not saved:
                raise ValueError("No initialized run in output directory")
            info = json.loads(saved[0])
        ranges = list(self.db.execute("SELECT start,stop FROM chunks ORDER BY start"))
        covered = sum(stop - first for first, stop in ranges)
        complete = covered == info["end_block"] - info["start_block"] + 1
        groups, daily_metrics = {}, {}
        methods = defaultdict(int)
        fields = ["date", "timestamp", "block_number", "transaction_hash", "log_index", "controller", "controller_name",
                  "kind", "name", "name_bytes_hex", "labelhash", "expires", "duration_seconds", "base_wei", "premium_wei", "revenue_wei",
                  "reported_cost_wei", "correction_wei", "refund_wei", "call_value_wei", "trace_address", "accounting_method",
                  "oracle_round_id", "eth_usd_answer", "referrer"]
        event_count, total_revenue, total_correction = 0, 0, 0
        event_tmp = self.root / "events.csv.gz.tmp"
        with event_tmp.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0, compresslevel=1) as zipped, io.TextIOWrapper(zipped, encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for (payload,) in self.db.execute("SELECT payload FROM events ORDER BY block_number,log_index"):
                event = load_event(payload)
                event.setdefault("name_bytes_hex", "0x" + event["name"].encode("utf-8").hex())
                writer.writerow(event)
                event_count += 1
                methods[event["accounting_method"]] += 1
                total_revenue += event["revenue_wei"]
                total_correction += event["correction_wei"]
                metric = daily_metrics.setdefault(event["date"], defaultdict(int))
                metric[event["kind"] + "_count"] += 1
                metric[event["kind"] + "_duration_seconds"] += event["duration_seconds"]
                for source, value in revenue_parts(event):
                    group = groups.setdefault((event["date"], source), defaultdict(int))
                    group["revenue_wei"] += value
                    group["event_count"] += 1
                    # Premium fees have no purchased duration of their own.
                    if source != "registration_premium":
                        group["duration_seconds"] += event["duration_seconds"]
                    if source == "renewal":
                        group["reported_cost_wei"] += event["reported_cost_wei"]
                        group["correction_wei"] += event["correction_wei"]
        event_tmp.replace(self.root / "events.csv.gz")
        # Upgrade an earlier uncompressed export only after the gzip is durable.
        (self.root / "events.csv").unlink(missing_ok=True)
        first_day = datetime.fromtimestamp(info["start_timestamp"], timezone.utc).date()
        last_day = datetime.fromtimestamp(info["end_timestamp"], timezone.utc).date()
        if complete:
            # Zero-fill only a fully acquired interval. Incomplete downloads are not zero revenue.
            for offset in range((last_day - first_day).days + 1):
                day = (first_day + timedelta(days=offset)).isoformat()
                daily_metrics.setdefault(day, defaultdict(int))
                for source in SOURCES:
                    groups.setdefault((day, source), defaultdict(int))
        tmp = self.root / "daily_revenue_by_source.csv.tmp"
        with tmp.open("w", newline="") as f:
            columns = ["date", "source", "currency", "revenue_wei", "revenue_eth", "event_count", "duration_seconds",
                       "reported_renewal_cost_wei", "renewal_overstatement_wei", "coverage"]
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for (day, source), group in sorted(groups.items()):
                # Boundary days of a block interval may cover only part of a UTC day.
                coverage = "complete_day" if complete and first_day.isoformat() < day < last_day.isoformat() else "partial_day"
                writer.writerow(dict(date=day, source=source, currency="ETH", revenue_wei=group["revenue_wei"],
                                     revenue_eth=eth(group["revenue_wei"]), event_count=group["event_count"],
                                     duration_seconds=group["duration_seconds"],
                                     reported_renewal_cost_wei=group["reported_cost_wei"] if source == "renewal" else "",
                                     renewal_overstatement_wei=group["correction_wei"] if source == "renewal" else "",
                                     coverage=coverage))
        tmp.replace(self.root / "daily_revenue_by_source.csv")
        write_daily_revenue(self.root)
        tmp = self.root / "daily_activity.csv.tmp"
        metric_columns = ["registration_count", "registration_duration_seconds", "renewal_count", "renewal_duration_seconds"]
        with tmp.open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["date", *metric_columns])
            writer.writeheader()
            for day, metric in sorted(daily_metrics.items()):
                writer.writerow({"date": day, **{k: metric[k] for k in metric_columns}})
        tmp.replace(self.root / "daily_activity.csv")
        csv_sum = sum(g["revenue_wei"] for g in groups.values())
        if csv_sum != total_revenue:
            raise ValueError("Daily revenue failed event-level reconciliation")
        manifest = dict(info, status="complete" if complete else "partial", last_error=error,
                        completed_block_ranges_half_open=ranges, event_count=event_count,
                        total_revenue_wei=str(total_revenue), total_revenue_eth=eth(total_revenue),
                        renewal_overstatement_wei=str(total_correction),
                        accounting_methods=dict(methods),
                        daily_revenue_layout="one row per UTC day; source columns in ETH; total_revenue_usd last",
                        journal_payload_encoding="JSON or Z1-prefixed zlib JSON",
                        legacy_split="unavailable: reported separately as registration_combined_legacy",
                        scope="Ethereum .eth permanent-registrar controller fees; excludes gas, secondary sales, 2017 auctions, subnames")
        manifest["files_sha256"] = {name: file_sha256(self.root / name)
                                    for name in ["events.csv.gz", "daily_revenue.csv", "daily_revenue_by_source.csv", "daily_activity.csv"]}
        if (self.root / "daily_eth_usd.csv").exists():
            previous = json.loads((self.root / "manifest.json").read_text())
            if previous["end_block_hash"] != info["end_block_hash"]:
                raise ValueError("Daily prices belong to a different snapshot")
            manifest["usd_conversion"] = previous["usd_conversion"]
            manifest["files_sha256"]["daily_eth_usd.csv"] = file_sha256(self.root / "daily_eth_usd.csv")
        else:
            manifest["usd_conversion"] = {"status": "not_indexed", "command": "ens-data prices"}
        atomic_json(self.root / "manifest.json", manifest)
        print(f"Exported {event_count:,} events; {eth(total_revenue)} ETH; range {manifest['status']}", flush=True)
