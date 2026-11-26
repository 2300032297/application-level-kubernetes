param([string]$Kind='kind',[string]$Velero='velero',[switch]$SkipBuild)
$ErrorActionPreference='Stop'
function Check([string]$Message) { if ($LASTEXITCODE -ne 0) { throw $Message } }
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    $labRoot=Join-Path $PWD 'runtime/lab'
    New-Item -ItemType Directory -Force $labRoot | Out-Null
    $env:KUBECONFIG=Join-Path $labRoot 'kubeconfig'
    foreach ($name in @('kr-source','kr-target')) {
        $node="$name-control-plane"
        $clusters=& $Kind get clusters
        Check 'Could not inspect Kind clusters'
        if ($name -notin $clusters) {
            & $Kind create cluster --name $name --kubeconfig $env:KUBECONFIG --image kindest/node:v1.34.0 --config deploy/kind.yaml --retain --wait 0s
            Check "Could not create $name"
        } else {
            $isRunning=& docker inspect $node --format '{{.State.Running}}'
            Check "Could not inspect $name"
            if ($isRunning.Trim() -ne 'true') { & docker start $node | Out-Null; Check "Could not restart $name" }
            & $Kind export kubeconfig --name $name --kubeconfig $env:KUBECONFIG
            Check "Could not export $name context"
        }
        $context="kind-$name"
        $apiReady=$false
        for ($attempt=0;$attempt -lt 60;$attempt++) {
            $probe=kubectl --context $context --request-timeout=5s get --raw /readyz 2>$null
            if ($LASTEXITCODE -eq 0 -and $probe -match 'ok') { $apiReady=$true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $apiReady) { throw "Project API did not become ready: $context" }
        # Each disposable cluster has one node. A static PTP CNI avoids an
        # additional CNI controller and provides outbound masquerading.
        # This lab network does not implement Kubernetes NetworkPolicy.
        $cidr=''
        for ($attempt=0;$attempt -lt 30;$attempt++) {
            $cidr=kubectl --context $context get node $node -o jsonpath='{.spec.podCIDR}' 2>$null
            if ($LASTEXITCODE -eq 0 -and $cidr) { break }
            Start-Sleep -Seconds 2
        }
        if (-not $cidr) { throw 'Project node has no assigned PodCIDR' }
        $oldCni=kubectl --context $context -n kube-system get daemonset kindnet --ignore-not-found -o name
        Check 'Could not inspect project CNI'
        if ($oldCni) {
            kubectl --context $context -n kube-system delete daemonset kindnet --wait=true
            Check 'Could not retire the old project CNI controller'
            & docker exec $node sh -c 'rm -f /etc/cni/net.d/10-kindnet.conflist'
            Check 'Could not retire the old project CNI configuration'
        }
        $cni=@{cniVersion='0.3.1';name='kr-lab';plugins=@(@{type='ptp';ipMasq=$true;ipam=@{type='host-local';dataDir='/run/cni-ipam-state';ranges=@(,@(@{subnet=$cidr}));routes=@(@{dst='0.0.0.0/0'})}},@{type='portmap';capabilities=@{portMappings=$true}})}
        $cni | ConvertTo-Json -Depth 15 -Compress | docker exec -i $node sh -c 'cat > /etc/cni/net.d/10-kr-lab.conflist'
        Check 'Lab CNI configuration failed'
        kubectl --context $context wait --for=condition=Ready node/$node --timeout=300s
        Check 'Project node is not Ready'
        $storage=@{apiVersion='storage.k8s.io/v1';kind='StorageClass';metadata=@{name='kr-local'};provisioner='kubernetes.io/no-provisioner';volumeBindingMode='WaitForFirstConsumer'}
        $storage | ConvertTo-Json -Depth 20 | kubectl --context $context apply --validate=false -f -
        Check 'StorageClass setup failed'
        # Local PVs (not hostPath PVs) are supported by Velero FSB. Multiple
        # slots permit repeated runs without reusing a previous run's PVC.
        & docker exec $node sh -c 'for slot in $(seq 1 20); do mkdir -p /var/kr-data/$slot; chown 10001:10001 /var/kr-data/$slot; done'
        Check 'Node directory setup failed'
        $volumes=@()
        for ($slot=1;$slot -le 20;$slot++) {
            $pv=@{apiVersion='v1';kind='PersistentVolume';metadata=@{name="kr-local-$slot"};spec=@{capacity=@{storage='1Gi'};volumeMode='Filesystem';accessModes=@('ReadWriteOnce');persistentVolumeReclaimPolicy='Retain';storageClassName='kr-local';local=@{path="/var/kr-data/$slot"};nodeAffinity=@{required=@{nodeSelectorTerms=@(@{matchExpressions=@(@{key='kubernetes.io/hostname';operator='In';values=@($node)})})}}}}
            $volumes+=$pv
        }
        @{apiVersion='v1';kind='List';items=$volumes} | ConvertTo-Json -Depth 30 | kubectl --context $context apply --validate=false -f - | Out-Null
        Check 'Local PV setup failed'
    }
    $authFile=Join-Path $labRoot 's3.json'
    if (-not (Test-Path -LiteralPath $authFile)) {
        $password=[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N')
        @{identities=@(@{name='kuberecover';credentials=@(@{accessKey='kuberecover';secretKey=$password});actions=@('Admin','Read','Write','List','Tagging')})} | ConvertTo-Json -Depth 10 | Set-Content $authFile
        [IO.File]::WriteAllText((Join-Path $labRoot 'credentials'),"[default]`naws_access_key_id=kuberecover`naws_secret_access_key=$password`n")
        [IO.File]::WriteAllText((Join-Path $labRoot 'repository-password'),[guid]::NewGuid().ToString('N')+[guid]::NewGuid().ToString('N'))
    }
    docker compose -f deploy/s3.yaml up -d
    Check 'S3 object-store setup failed'
    $store=docker compose -f deploy/s3.yaml ps -q store
    $ready=$false
    for ($attempt=0;$attempt -lt 60;$attempt++) {
        try { Invoke-WebRequest 'http://127.0.0.1:9091/' -TimeoutSec 2 | Out-Null; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
    }
    if (-not $ready) { throw 'Object-store filer is not ready' }
    's3.bucket.create -name velero' | docker exec -i $store weed shell -master=localhost:9333 -filer=localhost:8888
    Check 'S3 bucket creation failed'
    python scripts/check-store.py --credentials (Join-Path $labRoot 'credentials') --output (Join-Path $labRoot 'store-check.json')
    Check 'S3 write/read/delete validation failed'
    $ip=docker inspect $store --format '{{(index .NetworkSettings.Networks "kind").IPAddress}}'
    Check 'Could not find S3 endpoint'
    foreach ($name in @('kr-source','kr-target')) {
        $context="kind-$name"
        $installed=kubectl --context $context -n velero get deployment velero --ignore-not-found -o name
        Check 'Cluster not reachable'
        if (-not $installed) {
            @{apiVersion='v1';kind='Namespace';metadata=@{name='velero'}} | ConvertTo-Json -Depth 5 | kubectl --context $context apply --validate=false -f -
            Check 'Velero namespace creation failed'
            kubectl --context $context -n velero create secret generic velero-repo-credentials "--from-file=repository-password=$(Join-Path $labRoot 'repository-password')" --dry-run=client -o json | kubectl --context $context apply --validate=false -f -
            Check 'Repository password setup failed'
            & $Velero --kubecontext $context install --provider aws --image velero/velero:v1.18.2 --plugins velero/velero-plugin-for-aws:v1.14.0 --bucket velero --secret-file (Join-Path $labRoot 'credentials') --use-node-agent --use-volume-snapshots=false --backup-location-config "region=us-east-1,s3ForcePathStyle=true,s3Url=http://${ip}:8333"
            Check 'Velero installation failed'
        }
        $mode=if ($name -eq 'kr-target') {'ReadOnly'} else {'ReadWrite'}
        # The Windows Velero CLI can emit backslashes in Linux hostPath
        # values. Normalize filesystem paths before waiting for node agents.
        $agent=kubectl --context $context -n velero get daemonset node-agent -o json | ConvertFrom-Json -AsHashtable
        Check 'Could not inspect node-agent paths'
        foreach ($volume in $agent.spec.template.spec.volumes) {
            if ($volume.hostPath) { $volume.hostPath.path=$volume.hostPath.path.Replace('\','/') }
        }
        foreach ($container in @($agent.spec.template.spec.containers)+@($agent.spec.template.spec.initContainers)) {
            if ($container) {
                foreach ($mount in $container.volumeMounts) { $mount.mountPath=$mount.mountPath.Replace('\','/') }
            }
        }
        $agentPatch=Join-Path $labRoot "node-agent-$name.json"
        @{spec=@{template=@{spec=$agent.spec.template.spec}}} | ConvertTo-Json -Depth 30 | Set-Content $agentPatch
        kubectl --context $context -n velero patch daemonset node-agent --type merge --patch-file $agentPatch
        Check 'Could not normalize node-agent Linux paths'
        $patch=Join-Path $labRoot "location-$name.json"
        @{spec=@{accessMode=$mode;config=@{region='us-east-1';s3ForcePathStyle='true';s3Url="http://${ip}:8333"}}} | ConvertTo-Json -Depth 5 | Set-Content $patch
        kubectl --context $context -n velero patch backupstoragelocation default --type merge --patch-file $patch
        Check 'Backup location configuration failed'
        kubectl --context $context -n velero rollout status deployment/velero --timeout=600s
        Check 'Velero server is not ready'
        kubectl --context $context -n velero rollout status daemonset/node-agent --timeout=600s
        Check 'Node agent not ready'
        kubectl --context $context -n velero wait '--for=jsonpath={.status.phase}=Available' backupstoragelocation/default --timeout=180s
        Check 'Backup storage location is not Available'
    }
    if (-not $SkipBuild) { docker build -t kube-recover:1.0.0 .; Check 'Image build failed' }
    foreach ($name in @('kr-source','kr-target')) { & $Kind load docker-image kube-recover:1.0.0 --name $name; Check 'Image load failed' }
    Write-Host 'Lab ready. KUBECONFIG is set for this PowerShell session.'
    Write-Host 'S3 API: http://localhost:9090. Object browser: http://localhost:9091/buckets/velero/'
    Write-Host 'Run: python -m kube_recover cluster --source-context kind-kr-source --target-context kind-kr-target --output runtime/cluster-run-1'
} finally { Pop-Location }
