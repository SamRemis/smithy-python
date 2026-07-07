#!/usr/bin/env python3
"""Latency benchmark: async vs sync client options for smithy-python.

Runs each client in the same process. Reports p50/p95/p99 latency and
throughput per client against a local HTTP mock.
"""

import argparse
import asyncio
import statistics
import time
import warnings

from clients import make_async_client, make_sync_b_client, make_sync_a_client

warnings.simplefilter("ignore")


def percentile(sorted_data, p):
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def bench_async(iterations: int, warmup: int) -> list[float]:
    from aws_sdk_polly.models import DescribeVoicesInput

    async def run_all():
        client = make_async_client()
        req = DescribeVoicesInput()
        for _ in range(warmup):
            await client.describe_voices(req)
        latencies = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            await client.describe_voices(req)
            latencies.append(time.perf_counter() - t0)
        return latencies

    return asyncio.run(run_all())


def _bench_sync(client_factory, iterations, warmup) -> list[float]:
    from aws_sdk_polly.models import DescribeVoicesInput

    client = client_factory()
    req = DescribeVoicesInput()
    for _ in range(warmup):
        client.describe_voices(req)
    latencies = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        client.describe_voices(req)
        latencies.append(time.perf_counter() - t0)
    return latencies


CLIENTS = {
    "async": bench_async,
    "sync_a": lambda i, w: _bench_sync(make_sync_a_client, i, w),
    "sync_b": lambda i, w: _bench_sync(make_sync_b_client, i, w),
}


def format_results(results, iterations):
    lines = [
        "",
        "| Client | p50 ms | p95 ms | p99 ms | mean ms | calls/sec |",
        "|--------|--------|--------|--------|---------|-----------|",
    ]
    for name, lats in results.items():
        s = sorted(lats)
        p50 = percentile(s, 50) * 1000
        p95 = percentile(s, 95) * 1000
        p99 = percentile(s, 99) * 1000
        mean = statistics.mean(lats) * 1000
        thr = iterations / sum(lats)
        lines.append(
            f"| {name:<6} | {p50:>6.3f} | {p95:>6.3f} | {p99:>6.3f} | {mean:>7.3f} | {thr:>9.1f} |"
        )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--clients", type=str, default="async,sync_a,sync_b")
    args = ap.parse_args()

    results = {}
    for name in [c.strip() for c in args.clients.split(",")]:
        if name not in CLIENTS:
            print(f"unknown client: {name}")
            continue
        print(f"benching {name}: warmup={args.warmup} iterations={args.iterations}...")
        results[name] = CLIENTS[name](args.iterations, args.warmup)
        med = statistics.median(results[name]) * 1000
        print(f"  median = {med:.3f} ms")

    if results:
        print(format_results(results, args.iterations))


if __name__ == "__main__":
    main()
