"""Verify long empty tails of retired contracts once, then cache each empty chunk.

No retirement date is assumed: a finalized, full-range Cryo query must return
zero matching events before any cache entries are created.
"""
import json
import os
from pathlib import Path

from .contracts import CONTROLLERS, BASE_REGISTRARS, BASE_REGISTERED, BASE_MIGRATED, BASE_RENEWED, REGISTERED, RENEWED
from .pipeline import Pipeline, atomic_json, file_sha256


def main():
    root = Path("data")
    pipeline = Pipeline(root, os.environ["ETH_RPC_URL"], rps=5, concurrency=1)
    info = json.loads(pipeline.db.execute("SELECT value FROM meta WHERE key='run'").fetchone()[0])
    pipeline.initialize(info["start_block"], info["end_block"])
    latest = pipeline.db.execute("SELECT max(stop) FROM chunks").fetchone()[0] or info["start_block"]
    start = latest + 2 * info["chunk_size"]  # stay ahead of the running acquisition
    stop = info["end_block"] + 1
    if start >= stop:
        return
    targets = [("logs", c.name, c.address, [REGISTERED["combined"], RENEWED]) for c in CONTROLLERS[:2]]
    targets.append(("base_logs", BASE_REGISTRARS[0][0], BASE_REGISTRARS[0][0],
                    [BASE_REGISTERED, BASE_MIGRATED, BASE_RENEWED]))
    for dataset, name, address, topics in targets:
        proof_key = f"empty-proof-{name}-{start}-{stop}"
        frame = pipeline.collect(dataset, proof_key, _dataset="logs", blocks=[f"{start}:{stop}"],
                                 contract=[address], topic0=topics, inner_request_size=stop-start)
        if len(frame):
            print(f"{name}: nonempty tail; leaving normal extraction in place", flush=True)
            continue
        proof = pipeline.raw / dataset / f"{proof_key}.parquet"
        count = 0
        for first in range(start, stop, info["chunk_size"]):
            last = min(first + info["chunk_size"], stop)
            destination = pipeline.raw / dataset / f"{name}-{first}-{last}.parquet"
            try:
                os.link(proof, destination)
                count += 1
            except FileExistsError:
                pass
        atomic_json(proof.with_suffix(".json"), dict(address=address, topic0=topics, start_block=start,
                    stop_block_exclusive=stop, end_block_hash=info["end_block_hash"],
                    rows=0, linked_chunks=count, proof_sha256=file_sha256(proof)))
        print(f"{name}: verified empty tail; cached {count} chunks", flush=True)


if __name__ == "__main__":
    main()
