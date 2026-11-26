# Abstract to implementation mapping

| Capstone promise | Implementation | Acceptance |
|---|---|---|
| Application-level backup | SQLite Online Backup API locally; Velero FSB/Kopia on Kubernetes PVCs | Artifact integrity plus complete canonical state |
| Restore | Verified staging locally; native Velero Restore in a fresh namespace | Schema, data, business totals and health |
| Recovery across application versions | Explicit 1.0 to 2.0 database converter in schema.py | Every logical record preserved across different schemas |
| Compatibility checking | Version allowlist, exact schema columns, customer resolution, FK and amount checks | Unknown versions and unsupported downgrades rejected |
| Post-recovery validation | Canonical table SHA-256, counts, totals, integrity, target version | Conjunction of checks; no averaging away failures |
| Kubernetes migration | Distinct source/target cluster UID checks, namespace mapping and PVC-class mapping | Native backup/restore and volume-transfer evidence |
| Docker | Nonroot application image and Docker Compose dashboard | Container health and functional recovery |
| Velero and kubectl | Native Backup/Restore CRs and explicit-context kubectl adapter | Actual Kubernetes status retained |
| Cloud storage | Shared S3-compatible BackupStorageLocation; SeaweedFS local lab | Both clusters use the same bucket/prefix |
| MinIO inspection and application snapshot storage | Pinned source-built MinIO, private generated login, credential reveal, signed S3 client, object browser | Actual login, signed probe, uploaded-object listing, exact download and imported-backup recovery |
| Demonstrable pipeline | Explicit simulation, real local and real S3 modes; separate live cluster adapter | Simulation never accepts a recovery; real modes retain actual outcomes and HTTP probes |
| Larger demonstration data | 50,000 bundled orders and generator up to 100,000 | Actual 50,000-order recovery, complete hashes and 202,321-record counts |
| Availability and downtime objective | Reject invalid recovery, publish verified local target, measure recovery duration | Report measured scope; no zero-downtime or cloud-speed claims |

The abstract's reference to compatible application versions is interpreted as an application schema/data-contract requirement. Velero already supports migration, hooks and resource modifiers. This project's added layer supplies a specific application-version converter and semantic acceptance; it does not claim that Velero has no native compatibility facilities.

The earlier CSI/Kopia comparison and synthetic millisecond results are not inherited by the revised manuscript. Its supported scope is the implemented v1/v2 order application. This keeps the paper's implemented features consistent with the project rather than promising arbitrary application or database conversions.

The new MinIO functional acceptance case is separate from the retained SeaweedFS/Velero native case. The console does not silently change cluster storage configuration, and its illustrative simulation does not count as a live Kubernetes run. Generated credentials are available only through local runtime configuration and the explicit UI reveal action; no actual secret is embedded in the manuscript or clean source archive.
