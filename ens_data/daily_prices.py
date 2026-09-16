"""Chainlink UTC closing prices and exact daily USD revenue conversion."""
from bisect import bisect_right
import csv
from datetime import datetime, timedelta, timezone
import json

from eth_abi import encode

from .contracts import signature
from .oracle import ANSWER_UPDATED, FEED


def usd_amount(wei, quote):
    if wei == 0:
        return "0.00"
    if not quote or quote["status"] not in ("close", "snapshot_cutoff"):
        return ""
    answer, decimals = int(quote["answer"]), int(quote["decimals"])
    if wei < 0 or answer <= 0 or decimals != 8:
        raise ValueError("Invalid daily USD conversion inputs")
    denominator = 10 ** (18 + decimals)
    cents = (wei * answer * 100 + denominator // 2) // denominator
    return f"{cents // 100}.{cents % 100:02d}"


def read_prices(root):
    path = root / "daily_eth_usd.csv"
    if not path.exists():
        return {}
    result = {}
    with path.open(newline="") as stream:
        for row in csv.DictReader(stream):
            if row["date"] in result:
                raise ValueError("Duplicate daily price date")
            result[row["date"]] = row
    return result


def closing_rows(days, records, end_timestamp):
    """Use the last price observable by midnight, including phase-switch seeds."""
    ordered = sorted(records, key=lambda r: (r["available_at"], r["block_number"], r["log_index"]))
    positions = [r["available_at"] for r in ordered]
    for day in days:
        next_day = int((datetime.fromisoformat(day).replace(tzinfo=timezone.utc) + timedelta(days=1)).timestamp())
        cutoff = min(next_day - 1, end_timestamp)
        index = bisect_right(positions, cutoff) - 1
        row = dict(date=day, eth_usd_close="", answer="", decimals=8, as_of_timestamp=cutoff,
                   price_updated_at="", price_age_seconds="", phase_id="", aggregator="",
                   block_number="", transaction_hash="", log_index="", status="unavailable")
        if index >= 0:
            price = ordered[index]
            age = cutoff - price["updated_at"]
            if age < 0:
                raise ValueError("Closing price comes from the future")
            row.update(answer=price["answer"], price_updated_at=price["updated_at"],
                       price_age_seconds=age, phase_id=price["phase_id"], aggregator=price["aggregator"],
                       block_number=price["block_number"], transaction_hash=price["transaction_hash"],
                       log_index=price["log_index"])
            row["eth_usd_close"] = f"{price['answer'] // 10**8}.{price['answer'] % 10**8:08d}"
            row["status"] = "close" if cutoff == next_day - 1 else "snapshot_cutoff"
            if age > 86400:
                row["status"] = "stale"
                row["eth_usd_close"] = ""
        yield row


class DailyPrices:
    def __init__(self, pipeline, info):
        self.p = pipeline
        self.info = info
        self.directory = pipeline.raw / "daily_prices"
        self.directory.mkdir(exist_ok=True)
        path = self.directory / "states.json"
        self.states = json.loads(path.read_text()) if path.exists() else {}
        self.blocks = {}

    def save(self):
        from .pipeline import atomic_json
        atomic_json(self.directory / "states.json", self.states)

    def call(self, address, method, block, args=b""):
        key = f"{address}:{method}:{block}:{args.hex()}"
        if key not in self.states:
            self.states[key] = self.p.rpc.call("eth_call", [dict(to=address, data=signature(method)[:10] + args.hex()), hex(block)])
        return bytes.fromhex(self.states[key][2:])

    def integer(self, address, method, block):
        value = self.call(address, method, block)
        return int.from_bytes(value, "big", signed=method == "latestAnswer()") if value else 0

    def block(self, number):
        if number not in self.blocks:
            self.blocks[number] = self.p.rpc.block(number)
        return self.blocks[number]

    def timestamp(self, block):
        return int(self.block(block)["timestamp"], 16)

    def phases(self):
        from .pipeline import atomic_json
        end = self.info["end_block"]
        path = self.directory / "phases.json"
        phases = []
        if path.exists():
            saved = json.loads(path.read_text())
            if saved["feed"] != FEED or saved["end_block"] > end:
                raise ValueError("Daily price history belongs to a different snapshot")
            if self.block(saved["end_block"])["hash"] != saved["end_block_hash"]:
                raise ValueError("Saved price anchor hash changed")
            if saved["end_block"] == end:
                return saved["phases"]
            phases = saved["phases"]
        count = self.integer(FEED, "phaseId()", end)
        if count < len(phases):
            raise ValueError("Chainlink phase count decreased")
        for phase in range(len(phases) + 1, count + 1):
            lo, hi = 0, end
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if self.integer(FEED, "phaseId()", mid) >= phase:
                    hi = mid
                else:
                    lo = mid
            address = "0x" + self.call(FEED, "phaseAggregators(uint16)", end, encode(["uint16"], [phase]))[-20:].hex()
            phases.append(dict(phase=phase, address=address, activation_block=hi, activation_timestamp=self.timestamp(hi)))
        atomic_json(path, dict(feed=FEED, end_block=end, end_block_hash=self.info["end_block_hash"], phases=phases))
        self.save()
        return phases

    def logs(self, phase, address, start, stop, suffix=""):
        import polars as pl
        directory = self.p.raw / "daily_price_logs"
        key = f"phase-{phase}-{start}-{stop}{suffix}"
        target = directory / f"{key}.parquet"
        # The last active phase grows on update. Reuse its frozen prefix and
        # download only the tail, including when the phase ended in the meantime.
        if not target.exists():
            candidates = []
            for path in directory.glob(f"phase-{phase}-{start}-*{suffix}.parquet"):
                right = path.stem.removeprefix(f"phase-{phase}-{start}-")
                if suffix:
                    right = right.removesuffix(suffix)
                if right.isdigit() and start < int(right) < stop:
                    candidates.append((int(right), path))
            if candidates:
                old_stop, path = max(candidates)
                tail = self.logs(phase, address, old_stop, stop, suffix)
                frame = pl.read_parquet(path)
                if tail:
                    frame = pl.concat([frame, pl.DataFrame(tail, schema=frame.schema)], how="vertical_relaxed")
                temporary = target.with_suffix(".tmp")
                frame.write_parquet(temporary)
                temporary.replace(target)
                return frame.to_dicts()
        return self.p.collect("daily_price_logs", key, _dataset="logs",
                              blocks=[f"{start}:{stop}"], contract=[address], topic0=[ANSWER_UPDATED],
                              inner_request_size=min(self.p.log_request_size, 10000)).to_dicts()

    def acquire(self):
        if self.block(self.info["end_block"])["hash"] != self.info["end_block_hash"]:
            raise ValueError("Snapshot block hash changed")
        phases = self.phases()
        records = []
        for i, phase in enumerate(phases):
            number, address = phase["phase"], phase["address"]
            start = min(7000000, self.info["start_block"]) if i == 0 else phase["activation_block"]
            stop = phases[i + 1]["activation_block"] if i + 1 < len(phases) else self.info["end_block"] + 1
            emitter = address
            if number == 2:
                # Phase 2 adapts the phase-1 V2 aggregator; it emits no prices itself.
                emitter = "0x" + self.call(address, "aggregator()", stop - 1)[-20:].hex()
                if emitter != phases[0]["address"]:
                    raise ValueError("Unexpected legacy Chainlink adapter")
            rows = self.logs(number, emitter, start, stop, "-underlying" if emitter != address else "")
            phase_records = []
            for log in rows:
                answer = int.from_bytes(bytes.fromhex(log["topic1"][2:]), "big", signed=True)
                updated = int(log["data"], 16)
                if answer <= 0 or updated <= 0 or not start <= log["block_number"] < stop:
                    raise ValueError("Invalid Chainlink price log")
                phase_records.append(dict(available_at=updated, updated_at=updated, answer=answer, phase_id=number,
                                          aggregator=emitter, block_number=log["block_number"],
                                          transaction_hash=log["transaction_hash"], log_index=log["log_index"]))
            # Capture a newly activated feed's existing answer, even before its first active update.
            if i:
                if self.integer(address, "decimals()", start) != 8:
                    raise ValueError("Unexpected ETH/USD price precision")
                answer = self.integer(address, "latestAnswer()", start)
                updated = self.integer(address, "latestTimestamp()", start)
                if answer <= 0 or not 0 < updated <= phase["activation_timestamp"]:
                    raise ValueError("Invalid phase activation price")
                phase_records.append(dict(available_at=phase["activation_timestamp"], updated_at=updated, answer=answer,
                                          phase_id=number, aggregator=emitter, block_number=start,
                                          transaction_hash="", log_index=2**32))
            phase_records.sort(key=lambda r: (r["block_number"], r["log_index"]))
            if any(a["available_at"] > b["available_at"] for a, b in zip(phase_records, phase_records[1:])):
                raise ValueError("Price update timestamps are not ordered by block")
            if phase_records:
                for record in [phase_records[0], phase_records[-1]]:
                    if record["available_at"] != self.timestamp(record["block_number"]):
                        raise ValueError("Price update timestamp differs from its block")
                last = phase_records[-1]
                # V2 calls also work for the old contracts at historical blocks.
                if (self.integer(address, "latestAnswer()", stop - 1), self.integer(address, "latestTimestamp()", stop - 1)) != (last["answer"], last["updated_at"]):
                    raise ValueError("Price logs do not reconcile with phase end state")
            records.extend(phase_records)
            self.save()
            print(f"Chainlink phase {number}: {len(rows):,} updates reconciled", flush=True)
        return records

    def validate_closes(self, rows):
        """Independently check a UTC close per phase using historical contract state."""
        checked = set()
        for row in rows:
            phase = row["phase_id"]
            if row["status"] != "close" or phase in checked:
                continue
            lo, hi = int(row["block_number"]), self.info["end_block"]
            cutoff = row["as_of_timestamp"]
            # Interpolation narrows the day-end block quickly; binary bounds guarantee termination.
            while hi - lo > 1:
                left, right = self.timestamp(lo), self.timestamp(hi)
                mid = lo + (cutoff - left) * (hi - lo) // max(1, right - left)
                mid = max(lo + 1, min(hi - 1, mid))
                if self.timestamp(mid) <= cutoff:
                    lo = mid
                else:
                    hi = mid
            address = row["aggregator"] if phase == 1 else FEED
            if self.integer(address, "latestAnswer()", lo) != row["answer"]:
                raise ValueError("Daily close disagrees with historical feed state")
            checked.add(phase)
        self.save()
        return sorted(checked)


def enrich(pipeline):
    from .pipeline import atomic_json, file_sha256, write_daily_revenue
    info = json.loads((pipeline.root / "manifest.json").read_text())
    with (pipeline.root / "daily_revenue.csv").open(newline="") as stream:
        days = [row["date"] for row in csv.DictReader(stream)]
    if not days:
        raise ValueError("No daily revenue rows to price")
    prices = DailyPrices(pipeline, info)
    records = prices.acquire()
    rows = list(closing_rows(days, records, info["end_timestamp"]))
    checked = prices.validate_closes(rows)
    path = pipeline.root / "daily_eth_usd.csv"
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)
    write_daily_revenue(pipeline.root)
    info["daily_revenue_layout"] = "one row per UTC day; source columns in ETH; total_revenue_usd last"
    info["usd_conversion"] = dict(source="Chainlink ETH/USD", feed=FEED, timezone="UTC",
                                  method="last available price before midnight; final day capped at snapshot timestamp",
                                  rounding="nearest cent, half up", maximum_price_age_seconds=86400,
                                  missing_price="blank USD for positive ETH revenue; zero ETH remains 0.00 USD",
                                  first_price_timestamp=min((r["available_at"] for r in records), default=None),
                                  historical_close_check_phases=checked)
    for name in ["daily_eth_usd.csv", "daily_revenue.csv"]:
        info["files_sha256"][name] = file_sha256(pipeline.root / name)
    atomic_json(pipeline.root / "manifest.json", info)
    print(f"Exported {len(rows):,} daily prices; {sum(r['status'] == 'unavailable' for r in rows):,} unavailable and {sum(r['status'] == 'stale' for r in rows):,} stale dates", flush=True)
