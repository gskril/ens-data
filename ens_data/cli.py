import argparse
import os

from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description="Historical ENS cash revenue via Cryo; block bounds are inclusive.")
    parser.add_argument("command", choices=["run", "export", "prices"])
    parser.add_argument("--start-block", type=int, default=7_000_000)
    parser.add_argument("--end-block", type=int, help="Defaults to finalized; frozen in manifest for reproducible resumes")
    parser.add_argument("--output", default="data")
    parser.add_argument("--requests-per-second", type=int, default=int(os.getenv("ENS_REQUESTS_PER_SECOND", "1")))
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--chunk-size", type=int, default=10_000)
    parser.add_argument("--log-request-size", type=int, default=20_000,
                        help="Blocks per eth_getLogs request (use 10 for Alchemy free tier)")
    parser.add_argument("--trace-only", action="store_true", help="Trace every affected renewal instead of rebuilding oracle rounds")
    args = parser.parse_args()
    url = os.getenv("ETH_RPC_URL")
    if args.command in ("run", "prices") and not url:
        parser.error("Set ETH_RPC_URL to an Ethereum mainnet RPC supporting historical trace_transaction")
    if min(args.requests_per_second, args.concurrency, args.chunk_size, args.log_request_size) < 1:
        parser.error("Rate, concurrency and chunk size must be positive")
    pipeline = Pipeline(args.output, url, args.requests_per_second, args.concurrency, args.chunk_size, args.log_request_size, args.trace_only)
    if args.command == "run":
        pipeline.run(args.start_block, args.end_block)
    elif args.command == "prices":
        from .daily_prices import enrich
        enrich(pipeline)
    else:
        pipeline.export()


if __name__ == "__main__":
    main()
