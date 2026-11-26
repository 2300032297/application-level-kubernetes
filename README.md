# Application-Level Kubernetes Backup, Restore and Migration

Recover a versioned order-management application across Kubernetes clusters and application schemas. The title and abstract in `docs/ABSTRACT.txt` reproduce the capstone PDF; the dashboard and manuscript use the same title.

## Start the application

Requires Python 3.11 or newer with its standard SQLite module. No pip packages are needed.

```powershell
python -m kube_recover console
```

Open http://127.0.0.1:8090. A fresh workspace loads **50,000 orders, 2,000 customers, 80 products and 150,241 line items** (202,321 logical records). All are deterministic synthetic records. Existing workspaces keep their source; use **Application data → Generate source data** to create 5,000–100,000 orders. Saved backups and the previous target are retained. Use **Back up & migrate to v2**, or choose a guided pipeline mode. Browse/search both environments, export CSVs, inspect backups, inject a target fault, and recover a saved backup. Runtime files stay under `runtime/`.

## MinIO dashboard and login

Requires Docker Desktop with its Linux engine running. From this project directory:

```powershell
.\scripts\setup-minio.ps1
python -m kube_recover console
```

Alternatively use `.\scripts\start.ps1 -Minio`. The first setup builds the official MinIO source release `RELEASE.2025-10-15T17-29-55Z` with Go 1.24.8; compilation takes longer than pulling a prebuilt image. Subsequent starts reuse the image. The community repository is archived/source-only, so this isolated capstone setup does not depend on the removed `minio/minio` Docker Hub image. Source: https://github.com/minio/minio. MinIO's AGPLv3 license and upstream source remain applicable; the server is a separately built, unmodified service.

Open **Object storage** in the application. Choose **Connect & verify**, then **Reveal credentials** to see the generated local username/access key and password/secret key. **Open MinIO Console** opens http://127.0.0.1:9001; sign in with those generated credentials. The S3 API is http://127.0.0.1:9000. The console creates and validates the `application-backups` bucket, lists objects with pagination/prefix filtering, downloads objects, and uploads protected snapshots with exact read-back checks. Credentials are stored only in `runtime/minio.env` and `runtime/storage.json`, masked by default, and omitted from ordinary status/evidence exports. Revealing requires the console's CSRF token and secrets auto-hide after 30 seconds. They are not printed in setup logs or embedded in the paper.

The separately validated Velero two-cluster lab continues to use SeaweedFS and the `velero` bucket. The UI identifies the provider. MinIO application snapshots use the `applications/` prefix; they must not be described as native Velero volume backups. To connect the Docker application console to host MinIO, use `http://host.docker.internal:9000` as its API endpoint and enter the generated credentials in Object storage; the browser-console URL remains `http://127.0.0.1:9001`.

## Guided end-to-end pipeline

- **Simulation:** animated illustrative stages; no backup, storage call, Kubernetes operation, or accepted recovery is fabricated. Failure scenarios stop at a clearly simulated rejection.
- **Real local recovery:** actual SQLite backup, supported conversion, semantic verification and 12 loopback HTTP order probes. Storage stages are explicitly skipped.
- **Real MinIO / S3 round trip:** uploads a real database and manifest, downloads and verifies both against separately retained source expectations, imports those downloaded bytes, recovers them to v2, and executes 12 HTTP probes. Configure storage first.

Success, controlled data-fault detection/repair and unsupported-version rejection can be demonstrated. A selected storage-outage scenario intentionally blocks the transfer in storage mode; it does not disable a real server. Native Kubernetes execution remains in its separate tab and requires prepared clusters. Each pipeline saves structured evidence under `runtime/pipelines/` and can be exported from the UI. The overview adds actual monthly order counts and status distributions from the current source database.

Alternatively, run `docker compose up -d --build` and open the same URL. Stop a Python console using that port before starting Docker. The Docker console runs the local application workflow; run the Python console from a terminal with kubectl access to use the Kubernetes tab.

## Supported application versions

| Source | Target | Behavior |
|---|---|---|
| 1.0 | 1.0 | Verified same-version restore |
| 1.0 | 2.0 | Resolve customer email to customer ID, rename monetary field, add INR currency |
| 2.0 | 2.0 | Verified same-version restore |
| 2.0 | 1.0 | Rejected; no approved downgrade converter |
| Unknown | Any | Rejected |

Version 2 has a genuinely different schema, not just a different Kubernetes `apiVersion`. SHA-256 of the canonical logical records must match across versions. Foreign keys, line totals, counts and SQLite integrity must pass. The local workflow stages and verifies recovery before replacing the target. Hashes detect accidental corruption; they are not digital signatures.

## Real Kubernetes and Velero workflow

See `docs/KUBERNETES.md`. Requires Docker Desktop/Engine, kubectl, Kind v0.30.0 and Velero CLI v1.18.2. The included setup creates **only** `kr-source` and `kr-target`, configures supported local PVs, starts SeaweedFS, installs Velero/Kopia node agents and loads the application image.

```powershell
.\scripts\install-tools.ps1
.\scripts\setup-lab.ps1
python -m kube_recover cluster --source-context kind-kr-source --target-context kind-kr-target --output runtime/cluster-run-1
```

This executes real Velero backup and cross-cluster PVC restore, stops the restored v1 workload, converts its data to v2, starts v2 and runs 100 HTTP service probes plus complete semantic verification. The SeaweedFS S3-compatible repository is at http://localhost:9090 and its object browser at http://localhost:9091/buckets/velero/. Generated lab credentials remain in `runtime/lab/`; they are not part of this delivery.

## Tests and reproducible experiments

```powershell
python -m unittest discover -s tests -v
python -m kube_recover benchmark --output runtime/benchmark-1 --trials 30 --orders 5000
```

Use a new output directory for each benchmark. `summary.json`, raw `trials.jsonl` and per-request latencies are generated from that run. Local timings describe SQLite/filesystem/loopback operations, not Velero, CSI or cloud performance. The manuscript's evidence is provided separately from the runnable project so runtime folders stay clean.

## Other commands

```powershell
python -m kube_recover demo --root runtime/cli-demo --orders 5000
python -m kube_recover seed --output runtime/new-v1.db --orders 50000
python -m kube_recover inspect --database data/orders-v1.db
python -m kube_recover inspect --database data/orders-v2.db
```

`convert` is an internal, in-place transactional operation intended for a restored/staged database. Use the console's recovery operation for staged publication. Do not run a converter against an unrelated or actively written production database.

## Project structure

```text
kube_recover/   Versioned app, recovery engine, UI, Velero adapter, CLI and benchmark
data/           v1/v2 reference databases, four CSV datasets, dataset manifest
deploy/         Local cluster and SeaweedFS definitions
scripts/        Start, tests and isolated lab setup
tests/          Automated correctness, rejection and HTTP checks
docs/           Original abstract, implementation mapping and Kubernetes guide
```

The supported workload is this versioned SQLite order application. Arbitrary database upgrades, automatic downgrade conversion, write-traffic cutover, CSI comparison and managed-cloud performance are not claimed. SeaweedFS supplies an S3-compatible local test repository; using a remote S3 service requires its endpoint, credentials and network configuration. The local HTTP console binds to loopback by default; it has no multi-user authentication and should not be exposed publicly.
