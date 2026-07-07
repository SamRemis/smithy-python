#!/usr/bin/env python3
"""RSS benchmark: async vs sync client options for smithy-python.

Spawns a fresh subprocess per client so each client's RSS is measured against
its own clean baseline. Parent process polls ps -o rss=` for the child at a
fixed interval and reports peak / p50 / delta.

Assumes the mock server is already running.
"""

import argparse
import os
import statistics
import subprocess
import sys
import warnings
import asyncio

warnings.simplefilter("ignore")


# ---------- child mode: run one client and hold the process alive briefly ----

def _run_child(client: str, iterations: int, warmup: int) -> None:
    from clients import make_async_client, make_sync_b_client, make_sync_a_client
    from aws_sdk_polly.models import DescribeVoicesInput

    if client == "async":
        async def run():
            c = make_async_client()
            req = DescribeVoicesInput()
            for _ in range(warmup):
                await c.describe_voices(req)
            print(f"CHILD READY pid={os.getpid()}", flush=True)
            for _ in range(iterations):
                await c.describe_voices(req)
            print("CHILD DONE", flush=True)

        asyncio.run(run())
        return

    factory = {"sync_a": make_sync_a_client, "sync_b": make_sync_b_client}[client]
    c = factory()
    req = DescribeVoicesInput()
    for _ in range(warmup):
        c.describe_voices(req)
    print(f"CHILD READY pid={os.getpid()}", flush=True)
    for _ in range(iterations):
        c.describe_voices(req)
    print("CHILD DONE", flush=True)


# ---------- parent mode: spawn each client and sample RSS ---------------------

def rss_kib(pid: int) -> int | None:
    try:
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    s = out.stdout.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None


def sample_child(client: str, iterations: int, warmup: int, interval_ms: int) -> dict:
    """Spawn a child, wait for READY, sample RSS until DONE."""
    proc = subprocess.Popen(
        [sys.executable, __file__, "--child", client,
         "--iterations", str(iterations), "--warmup", str(warmup)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        bufsize=1,
    )

    baseline: int | None = None
    samples: list[int] = []
    interval = interval_ms / 1000.0

    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line.startswith("CHILD READY"):
            baseline = rss_kib(proc.pid)
            break

    # Sample during the timed call loop.
    active = True
    while active:
        if proc.poll() is not None:
            break
        r = rss_kib(proc.pid)
        if r is not None:
            samples.append(r)
        # Check for DONE without blocking the sampler thread indefinitely.
        try:
            proc.wait(timeout=interval)
            active = False
        except subprocess.TimeoutExpired:
            pass

    proc.wait()
    return {
        "client": client,
        "baseline": baseline,
        "samples": samples,
    }


def percentile(sorted_data, p):
    k = (len(sorted_data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(sorted_data):
        return sorted_data[f]
    return sorted_data[f] + (k - f) * (sorted_data[c] - sorted_data[f])


def format_results(rows):
    lines = [
        "",
        "| Client | baseline KiB | p50 KiB   | p95 KiB   | max KiB   | delta (max-base) |",
        "|--------|--------------|-----------|-----------|-----------|------------------|",
    ]
    for r in rows:
        s = sorted(r["samples"])
        if not s:
            lines.append(f"| {r['client']:<6} | (no samples) |")
            continue
        p50 = int(percentile(s, 50))
        p95 = int(percentile(s, 95))
        mx = max(s)
        base = r["baseline"] or 0
        delta = mx - base
        lines.append(
            f"| {r['client']:<6} | {base:>12,} | {p50:>9,} | {p95:>9,} | {mx:>9,} | {delta:>+16,} |"
        )
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--child", type=str, default=None,
                    help="internal: run as a benchmarked client subprocess")
    ap.add_argument("--iterations", type=int, default=1000)
    ap.add_argument("--warmup", type=int, default=100)
    ap.add_argument("--clients", type=str, default="async,sync_a,sync_b")
    ap.add_argument("--interval-ms", type=int, default=20)
    args = ap.parse_args()

    if args.child:
        _run_child(args.child, args.iterations, args.warmup)
        return

    rows = []
    for name in [c.strip() for c in args.clients.split(",")]:
        print(f"sampling {name}: warmup={args.warmup} iterations={args.iterations}...")
        r = sample_child(name, args.iterations, args.warmup, args.interval_ms)
        med = int(statistics.median(r["samples"])) if r["samples"] else 0
        print(f"  baseline={r['baseline']} KiB, median={med:,} KiB, samples={len(r['samples'])}")
        rows.append(r)

    print(format_results(rows))


if __name__ == "__main__":
    main()
