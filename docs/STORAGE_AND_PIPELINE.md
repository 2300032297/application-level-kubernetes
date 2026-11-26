# MinIO storage dashboard and guided recovery

Run `scripts/setup-minio.ps1` from the project directory, then start `python -m kube_recover console`. This creates only this project's `kr-minio` Compose services and persistent volume. It does not change the separate Velero lab or another project's containers.

## Storage workspace

The generated local connection is loaded automatically when the console uses the default `runtime` directory. Open **Object storage → Connect & verify**. The check creates the configured bucket if it does not exist, writes a unique probe, compares the exact returned bytes and deletes the probe. The browser then lists actual S3 keys, sizes and modification times. Listings support a prefix filter and continuation tokens, with up to 100 objects per page. Counts and bytes shown are for the current page.

**Reveal credentials** retrieves the local login using a CSRF-protected POST. The generated access key is also the MinIO console username; the generated secret is its password. **Open MinIO Console** opens the separate MinIO browser at `http://127.0.0.1:9001`. Secrets are masked by default, cleared when hidden and automatically hidden after 30 seconds. They are excluded from status, ordinary evidence and the project ZIP. Setup stores them under `runtime/`, which is ignored by packaging. No fixed shared password is shipped.

The Python host console uses `http://127.0.0.1:9000` for the MinIO API. A console running in Docker must instead use `http://host.docker.internal:9000`, with the generated credentials entered in its storage form. The browser URL remains `http://127.0.0.1:9001`. Connections use path-style S3 Signature Version 4 requests. Region defaults to `us-east-1`. HTTPS endpoints use normal certificate verification. The signed client refuses redirects. Console transfers have a 64 MiB object limit; multipart transfer is not implemented.

## Snapshot provenance

Application snapshots use `applications/<backup-id>/application.db` and `manifest.json`. Upload checks both objects by reading them back. Download is checked against the separately retained local backup checksum and manifest, then imported under a new backup identifier. The real storage pipeline recovers that imported artifact. Corrupted downloaded content is rejected before replacing the target. The UI does not label an S3 upload as a native Velero backup.

The independent Kubernetes lab continues to use SeaweedFS and its `velero` repository. Configuring MinIO in the dashboard does not silently repoint a cluster's BackupStorageLocation. The cluster tab executes the existing live Velero/PVC adapter after `scripts/setup-lab.ps1`; the UI's simulation never starts clusters or fabricates native transport evidence.

## Pipeline scenarios

Simulation illustrates ten stages, retains `simulated: true`, and never returns an accepted recovery. Its displayed elapsed times are illustrative run times, not measured recovery performance. Unsupported-version, data-fault and storage-outage scenarios stop at a simulated rejection.

Real local execution creates a consistent snapshot, explicitly skips upload/download, checks compatibility, converts and verifies the target, and runs 12 actual loopback HTTP order queries. Real storage execution additionally uploads and downloads the actual snapshot and uses the imported bytes for recovery. Both modes record actual step outcomes. The supported transition remains 1.0 → 2.0; unsupported targets fail explicitly.

In real fault mode, one target order total is changed, the inconsistency is detected and the saved snapshot is used to repair the target. The storage-outage scenario deliberately blocks its transfer stage; it does not stop a server or simulate a measured network partition. Local mode rejects the storage-outage scenario because it performs no storage transfer.

The overview aggregates real source records by month and order status. Application data can generate 5,000–100,000 synthetic source orders while retaining existing backups and the prior target. A newly generated source does not retroactively change a previously accepted target or its evidence. Pipeline records remain under `runtime/pipelines/` and can be exported individually from the UI.

## Stop and restart

`docker compose -f deploy/minio.yaml stop` stops this project's MinIO. `docker compose -f deploy/minio.yaml up -d --wait` starts it again without deleting the persistent volume. Re-running setup reuses the generated login. The source-pinned MinIO service is an isolated capstone dependency; upstream community maintenance status and license information are documented at https://github.com/minio/minio.
