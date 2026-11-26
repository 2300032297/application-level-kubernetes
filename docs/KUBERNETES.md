# Kubernetes run and verification

## Prerequisites

Use Docker Desktop with Linux containers or Docker Engine, Python 3.11+, PowerShell 7 for the lab scripts, kubectl v1.34.0, Kind v0.30.0 and Velero CLI v1.18.2. `scripts/install-tools.ps1` installs matching workspace-local tools and verifies their SHA-256 checksums. The setup uses Kubernetes v1.34.0, Velero server v1.18.2, AWS plugin v1.14.0 and SeaweedFS 3.85 lab image. These are repeatable lab pins, not a recommendation to deploy a lab object-store image on a public network. The retained live run used the host's kubectl v1.36 client and completed successfully with a version-skew warning; new installations use the matching v1.34 client.

The two cluster names `kr-source` and `kr-target` are reserved for this project. Use a machine with adequate free memory and disk; creating two clusters and pulling images can take several minutes. Existing unrelated applications are not stopped by the scripts. Kubernetes credentials are written to `runtime/lab/kubeconfig`, never to the user's default kubeconfig. Each disposable cluster uses a static PTP CNI suitable for its single node, with outbound masquerading; this small lab network does not implement NetworkPolicy.

Allow spare Docker memory for both control planes and the recovery services. Use an 8 GB Docker allocation when running this lab alongside other substantial container stacks; a shared 3.7 GB allocation caused resource pressure during validation. The setup normalizes Linux node-agent volume paths because the Windows Velero CLI can otherwise generate backslashes in hostPath values.

## Setup

From the project root, run:

```powershell
.\scripts\setup-lab.ps1
```

If the tools are not on PATH, pass `-Kind C:\path\kind.exe -Velero C:\path\velero.exe`. The script sets KUBECONFIG in the current terminal, configures twenty static local PVs per cluster and starts SeaweedFS on Docker's Kind network. The node agent accesses node volume paths to execute FSB. Source and target share a repository password; target BackupStorageLocation is ReadOnly. The password must stay unchanged while its Kopia backups are retained.

Local PVs are used because Velero FSB does not support hostPath PVs. Each run uses fresh namespaces and claims a free local PV. PVs have a Retain policy: deleting a namespace does not securely erase its data or automatically free its slot. After twenty runs, provision new slots or deliberately reclaim the exact project's volumes. Never clean unrelated volumes.

## Run

```powershell
python -m kube_recover cluster --source-context kind-kr-source --target-context kind-kr-target --output runtime/cluster-run-1 --orders 5000
```

For the dashboard's Kubernetes tab, start `python -m kube_recover console` in the same terminal after setup. Enter the contexts above and `kr-local` for both StorageClasses. Run records are written under the console runtime root's `cluster-runs/` directory. The Docker dashboard does not inherit host kubectl/kubeconfig access; use the host Python console for this operation.

## Acceptance evidence

The run directory contains source manifests, expected table hashes and totals, native Backup and Restore status, PodVolumeBackup and PodVolumeRestore status, schema converter logs, service latency samples and `result.json`.

A pass requires:

1. Distinct `kube-system` namespace UIDs on source and target.
2. Native Velero Backup and Restore phase Completed with no errors.
3. At least one Completed PodVolumeBackup and PodVolumeRestore; metadata-only backups fail.
4. Restored v1 semantic state matching source before conversion.
5. Target application version 2.0, matching all canonical table hashes and monetary totals.
6. Healthy target service and zero errors in 100 HTTP order queries.

`accepted: true` means every declared check passed. Failed operations write `accepted: false`, an error and the last stage. Failed cluster restores remain in their isolated namespaces for diagnosis. The source is retained. The adapter is an offline recovery workflow for a read-only validation window; it does not switch production traffic.

The static local-PV lab can emit nonfatal Velero warnings about excluded source PV metadata (target volumes are preprovisioned) or the target namespace keeping its own `kube-root-ca.crt` ConfigMap. These are recorded rather than suppressed. Acceptance still requires a bound target PVC, completed volume transfer and exact application-level state verification.

## Inspect

```powershell
kubectl --context kind-kr-source -n velero get backups,podvolumebackups
kubectl --context kind-kr-target -n velero get restores,podvolumerestores
kubectl --context kind-kr-target get pods,pvc -A
```

Open the local object browser at http://localhost:9091/buckets/velero/. S3 API credentials are generated in `runtime/lab/s3.json`; the loopback-only filer browser is a lab inspection interface. The `velero` bucket contains native Velero/Kopia repository data. Application tables are read through the restored application or exported CSV; they are not fabricated objects shown as Velero backup files.

## Remote storage

The recovery adapter reads the clusters' configured BackupStorageLocations. It can use a shared remote S3-compatible location when both clusters have the matching provider/plugin, bucket/prefix, credentials and repository password. Configure TLS, IAM and retention for the actual provider. Remote IAM, provider networking and managed-service operation require their own acceptance run; local SeaweedFS evidence does not prove them.

## Stop the disposable lab

Only when you no longer need the evidence and PVCs, use `kind delete cluster --name kr-source` and `kind delete cluster --name kr-target`. This destroys their lab PVC data. `docker compose -f deploy/s3.yaml down` stops SeaweedFS but retains its backup volume; omit `--volumes` to preserve backups. Keep the generated repository password with retained backups.
