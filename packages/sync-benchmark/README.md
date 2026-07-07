# sync-benchmark

Prototype benchmarks comparing async vs. sync client options for smithy-python.
Not a shipping package — a self-contained harness for evaluating sync SDK
approaches on a single branch (`prototype/sync-option-c-parallel`).

## What's being compared

Three client shapes, all invoking Polly `DescribeVoices` against a local HTTP
mock so network latency isn't in the numbers:

| Name     | Approach                                                                                    |
|----------|---------------------------------------------------------------------------------------------|
| `async`  | Baseline: the existing async `PollyClient` from `aws-sdk-python`.                           |
| `sync_a` | **Option A** — hand-written parallel sync stack (sync pipeline, sync CRT transport, etc.). |
| `sync_b` | **Option B** — typed sync wrapper (`PollyTypedSyncClient`) over the async client + a background event-loop thread. |

Option D (unasync / build-time transform) is not benchmarked because its output
runtime is functionally identical to Option A's — same code paths, same
primitives.

## Files

- `mock_server.py` — stdlib HTTP server on `localhost:8888` returning canned
  `DescribeVoices` JSON.
- `clients.py` — shared client factories used by both benchmarks.
- `bench_latency.py` — latency benchmark. Times each client in-process and
  prints p50/p95/p99/mean/throughput.
- `bench_rss.py` — RSS benchmark. Spawns each client in its own subprocess and
  samples `ps -o rss=` externally so numbers are per-client, not accumulated.
- `sync_facade.py` — Option B implementation: `PollyTypedSyncClient` (typed
  wrapper over a background event-loop thread).

## Repro instructions

Two sibling repos are required on the same prototype branch:

```
~/PycharmProjects/
├── smithy-python/       # branch: prototype/sync-option-c-parallel
└── aws-sdk-python/      # branch: prototype/sync-option-c-parallel
```

Both branches must be checked out simultaneously. Some editable installs from
smithy-python are consumed by the Polly client in aws-sdk-python.

### 1. Set up a venv and install everything editable

```bash
# From aws-sdk-python (or anywhere — venv path is what matters):
cd ~/PycharmProjects/aws-sdk-python
python3.12 -m venv .venv
source .venv/bin/activate

# smithy-python packages (the runtime and its extensions):
cd ~/PycharmProjects/smithy-python
uv pip install \
  -e packages/smithy-core \
  -e "packages/smithy-http[awscrt]" \
  -e packages/aws-sdk-signers \
  -e packages/smithy-aws-core \
  -e packages/smithy-json \
  -e packages/smithy-xml \
  -e packages/smithy-aws-event-stream

# Polly client (the generated SDK):
cd ~/PycharmProjects/aws-sdk-python
uv pip install -e clients/aws-sdk-polly
```

`uv pip` is used because it's much faster than pip; regular `pip install -e` also works.

### 2. Confirm both branches are on Option A

```bash
cd ~/PycharmProjects/smithy-python  && git rev-parse --abbrev-ref HEAD
cd ~/PycharmProjects/aws-sdk-python && git rev-parse --abbrev-ref HEAD
# Both should print: prototype/sync-option-c-parallel
```

If they aren't, `git checkout prototype/sync-option-c-parallel` in both.

### 3. Smoke-test Option A end-to-end

```bash
source ~/PycharmProjects/aws-sdk-python/.venv/bin/activate
cd ~/PycharmProjects/smithy-python/packages/sync-benchmark

./mock_server.py &
python3 -c "
import warnings; warnings.simplefilter('ignore')
from aws_sdk_polly.sync_client import PollySyncClient
from aws_sdk_polly.config import Config
from aws_sdk_polly.models import DescribeVoicesInput
from aws_sdk_polly._private.schemas import PARROT_V1
from smithy_http.sync.crt import AWSCRTHTTPClient
from smithy_aws_core.sync.endpoints.standard_regional import StandardRegionalEndpointsResolver
from smithy_aws_core.sync.protocols import RestJsonClientProtocol
from smithy_aws_core.sync.auth.sigv4 import SigV4AuthScheme
from smithy_aws_core.sync.identity.static import StaticCredentialsResolver
from smithy_core.shapes import ShapeID

config = Config(
    endpoint_uri='http://localhost:8888', region='us-east-1',
    endpoint_resolver=StandardRegionalEndpointsResolver(endpoint_prefix='polly'),
    protocol=RestJsonClientProtocol(PARROT_V1),
    transport=AWSCRTHTTPClient(),
    aws_credentials_identity_resolver=StaticCredentialsResolver(),
    aws_access_key_id='fake', aws_secret_access_key='fake',
)
config.auth_schemes = {ShapeID('aws.auth#sigv4'): SigV4AuthScheme(service='polly')}
result = PollySyncClient(config).describe_voices(DescribeVoicesInput())
print(f'SUCCESS voices={len(result.voices or [])}')
"
kill %1
```

Expected output: `SUCCESS voices=3`.

### 4. Run the benchmarks

Start the mock server in a separate terminal (or background it), then run
either script. Both accept the same core flags.

```bash
# terminal 1: mock server
./mock_server.py

# terminal 2: latency
./bench_latency.py --iterations 1000 --warmup 100

# terminal 2: RSS
./bench_rss.py --iterations 1000 --warmup 100
```

Or subset clients:

```bash
./bench_latency.py --clients async,sync_a
./bench_rss.py --clients async,sync_a
```

Flags:

- `--iterations N` — timed iterations per client (default 1000)
- `--warmup N` — warmup iterations discarded before timing (default 100)
- `--clients a,b,c` — comma-separated subset from `{async, sync_a, sync_b}`
- `--interval-ms N` — RSS sampling interval, `bench_rss.py` only (default 20)

## Sample results

Loopback, single-call sequential. Averages of 5 runs × 1000 iterations
(100 warmup) on an M-series Mac. Numbers vary run to run — treat as ballpark,
not exact.

**Latency**

| Client | p50 ms | p95 ms | p99 ms | mean ms | calls/sec |
|--------|--------|--------|--------|---------|-----------|
| async  | 0.977  | 1.157  | 1.257  | 0.977   | 1023.5    |
| sync_a | 0.740  | 0.884  | 0.970  | 0.755   | 1326.0    |
| sync_b | 1.012  | 1.239  | 1.354  | 1.026   | 975.1     |

**RSS** (peak resident memory during the timed loop)

| Client | baseline KiB | p50 KiB | max KiB | delta (max-base) |
|--------|--------------|---------|---------|------------------|
| async  | 38,477       | 38,862  | 39,066  | 589              |
| sync_a | 38,528       | 38,810  | 38,877  | 349              |
| sync_b | 38,509       | 38,699  | 38,803  | 294              |

The benchmarks were done using a local mocked HTTP server to reduce
network-level interference in these calls. On real AWS traffic (30–200 ms per
call), the deltas measured here (≤ 0.3 ms latency, ≤ 1 MiB RSS) become a
fraction of a percent of total call time / process footprint.

## Notes and caveats

- **Sequential, single-call.** These numbers don't reflect concurrency, where
  async wins by many multiples. Sync serializes; async can have N in flight.
- **`p99` is noisy.** With 2000 samples, tail measurements swing run-to-run.
  Don't overweight a single p99 number without repeated runs.
- **Warmup matters.** `sync_b` in particular shows the biggest first-vs-second-half
  median gap; give it at least 100 warmup iterations before trusting the number.
- **The mock is not zero-cost.** Loopback socket + kernel scheduling contributes
  a ~0.5–0.7 ms floor. Real per-call SDK overhead is smaller than the raw
  numbers suggest.
