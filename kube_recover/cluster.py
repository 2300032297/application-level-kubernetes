"""Real Velero FSB recovery followed by application schema conversion on the PVC."""
from __future__ import annotations
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid
from urllib.request import urlopen
from . import schema
from .util import write_json


class Kubectl:
    def __init__(self,context):
        if not context or context.startswith("-"):raise ValueError("Explicit context required")
        self.context=context
    def run(self,args,data=None,timeout=180):
        p=subprocess.run(["kubectl","--context",self.context,"--request-timeout=30s",*args],input=data,capture_output=True,timeout=timeout)
        if p.returncode:raise ValueError(p.stderr.decode(errors="replace")[-1800:])
        return p.stdout
    def json(self,args):return json.loads(self.run(args))
    def apply(self,obj):return self.run(["apply","--validate=false","-f","-"],json.dumps(obj).encode())


def wait_velero(k,kind,name,path,timeout=900):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        raw=k.run(["get",kind,name,"-n","velero","-o","json","--ignore-not-found"])
        obj=json.loads(raw) if raw.strip() else {}
        write_json(path,obj)
        status=obj.get("status",{});phase=status.get("phase")
        if phase in {"Failed","PartiallyFailed","FailedValidation","Canceled"}:raise ValueError(f"{kind}: {phase}; inspect {path}")
        if phase=="Completed":
            if status.get("errors",0):raise ValueError(f"{kind} completed with errors")
            return obj
        time.sleep(2)
    raise ValueError(f"Timed out waiting for {kind}/{name}")


def workload(namespace,storage,image,orders=5000):
    labels={"app":"order-management","app.kubernetes.io/managed-by":"kube-recover"}
    security={"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001}
    container={"name":"orders","image":image,"imagePullPolicy":"IfNotPresent",
               "command":["python","-m","kube_recover","workload","--version","1.0"],
               "ports":[{"containerPort":8080}],"volumeMounts":[{"name":"data","mountPath":"/data"}],
               "resources":{"requests":{"cpu":"50m","memory":"64Mi"},"limits":{"cpu":"1","memory":"512Mi"}},
               "readinessProbe":{"httpGet":{"path":"/healthz","port":8080},"periodSeconds":2},
               "securityContext":{"allowPrivilegeEscalation":False,"capabilities":{"drop":["ALL"]}}}
    init={"name":"seed","image":image,"imagePullPolicy":"IfNotPresent","command":["python","-c",f"from pathlib import Path; from kube_recover.schema import seed; p=Path('/data/application.db'); seed(p,{orders}) if not p.exists() else None"],
          "volumeMounts":[{"name":"data","mountPath":"/data"}]}
    return [{"apiVersion":"v1","kind":"Namespace","metadata":{"name":namespace,"labels":labels}},
            {"apiVersion":"v1","kind":"PersistentVolumeClaim","metadata":{"name":"app-data","namespace":namespace},"spec":{"storageClassName":storage,"accessModes":["ReadWriteOnce"],"resources":{"requests":{"storage":"1Gi"}}}},
            {"apiVersion":"apps/v1","kind":"Deployment","metadata":{"name":"orders","namespace":namespace},"spec":{"replicas":1,"selector":{"matchLabels":{"app":"order-management"}},"template":{"metadata":{"labels":labels,"annotations":{"backup.velero.io/backup-volumes":"data"}},"spec":{"automountServiceAccountToken":False,"securityContext":security,"initContainers":[init],"containers":[container],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"app-data"}}]}}}},
            {"apiVersion":"v1","kind":"Service","metadata":{"name":"orders","namespace":namespace},"spec":{"selector":{"app":"order-management"},"ports":[{"port":8080,"targetPort":8080}]}}]


def run_cluster(source_context,target_context,source_class="kr-local",target_class="kr-local",image="kube-recover:1.0.0",output="runtime/cluster",orders=5000,event=lambda p,m:print(p+": "+m,flush=True)):
    if source_context==target_context:raise ValueError("Select distinct clusters")
    if not 1<=orders<=100000:raise ValueError("Invalid order count")
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    if (out/"result.json").exists():raise ValueError("Choose a new run directory")
    src,dst=Kubectl(source_context),Kubectl(target_context)
    key="kr-"+uuid.uuid4().hex[:10];ns=key+"-source";target=key+"-target"
    result={"accepted":False,"engine":"velero-fsb-kopia","source_context":source_context,"target_context":target_context,"source_namespace":ns,"target_namespace":target,"backup_name":key,"restore_name":key+"-restore","source_version":"1.0","target_version":"2.0","image":image}
    def stage(p,m):result["stage"]=p;write_json(out/"result.json",result);event(p,m)
    try:
        stage("preflight","Checking distinct cluster identities, storage and shared backup location")
        ids=[k.json(["get","namespace","kube-system","-o","json"])["metadata"]["uid"] for k in (src,dst)]
        if ids[0]==ids[1]:raise ValueError("Contexts refer to the same cluster")
        locations=[]
        for k,sc in ((src,source_class),(dst,target_class)):
            k.run(["get","storageclass",sc]);b=k.json(["get","backupstoragelocation","default","-n","velero","-o","json"])
            if b.get("status",{}).get("phase")!="Available":raise ValueError("Velero backup location is unavailable")
            locations.append(b["spec"])
        if any(locations[0].get(k)!=locations[1].get(k) for k in ("provider","objectStorage")):raise ValueError("Clusters must share a backup bucket and prefix")
        if locations[1].get("accessMode")!="ReadOnly":raise ValueError("Target backup location must be ReadOnly")
        result["cluster_uids"]=ids
        resources=workload(ns,source_class,image,orders);write_json(out/"source-manifest.json",resources)
        stage("source","Deploying the version 1 order application")
        for r in resources:src.apply(r)
        src.run(["rollout","status","deployment/orders","-n",ns,"--timeout=240s"],timeout=260)
        expected=json.loads(src.run(["exec","-n",ns,"deployment/orders","--","python","-m","kube_recover","inspect","--database","/data/application.db"]))
        if expected["application_version"]!="1.0":raise ValueError("Unexpected source application schema")
        write_json(out/"expected-state.json",expected)
        result["expected"]=expected
        stage("backup","Creating a real Velero file-system backup")
        backup={"apiVersion":"velero.io/v1","kind":"Backup","metadata":{"name":key,"namespace":"velero"},"spec":{"includedNamespaces":[ns],"excludedResources":["persistentvolumes","storageclasses.storage.k8s.io"],"defaultVolumesToFsBackup":True,"snapshotVolumes":False,"ttl":"168h0m0s"}}
        src.apply(backup);wait_velero(src,"backup",key,out/"backup-status.json")
        volumes=src.json(["get","podvolumebackups","-n","velero","-l","velero.io/backup-name="+key,"-o","json"])
        write_json(out/"backup-volumes.json",volumes)
        if not volumes["items"] or any(i.get("status",{}).get("phase")!="Completed" for i in volumes["items"]):raise ValueError("No completed persistent-volume backup")
        stage("restore","Synchronizing backup and restoring version 1 into the target cluster")
        wait_velero(dst,"backup",key,out/"synced-backup.json")
        modifiers="version: v1\nresourceModifierRules:\n- conditions:\n    groupResource: persistentvolumeclaims\n  patches:\n  - operation: replace\n    path: /spec/storageClassName\n    value: \""+target_class+"\"\n"
        dst.apply({"apiVersion":"v1","kind":"ConfigMap","metadata":{"name":key+"-modifiers","namespace":"velero"},"data":{"resource-modifiers.yaml":modifiers}})
        restore={"apiVersion":"velero.io/v1","kind":"Restore","metadata":{"name":key+"-restore","namespace":"velero"},"spec":{"backupName":key,"namespaceMapping":{ns:target},"resourceModifier":{"kind":"ConfigMap","name":key+"-modifiers"}}}
        write_json(out/"restore-request.json",restore);dst.apply(restore);wait_velero(dst,"restore",key+"-restore",out/"restore-status.json")
        rv=dst.json(["get","podvolumerestores","-n","velero","-l","velero.io/restore-name="+key+"-restore","-o","json"]);write_json(out/"restore-volumes.json",rv)
        if not rv["items"] or any(i.get("status",{}).get("phase")!="Completed" for i in rv["items"]):raise ValueError("No completed persistent-volume restore")
        dst.run(["rollout","status","deployment/orders","-n",target,"--timeout=240s"],timeout=260)
        raw=json.loads(dst.run(["exec","-n",target,"deployment/orders","--","python","-m","kube_recover","inspect","--database","/data/application.db"]))
        if raw["semantic_sha256"]!=expected["semantic_sha256"]:raise ValueError("Restored v1 differs from source")
        stage("conversion","Stopping v1 and applying the approved v1-to-v2 converter on the restored PVC")
        conversion_start=time.perf_counter()
        dst.run(["scale","deployment/orders","-n",target,"--replicas=0"])
        dst.run(["wait","--for=delete","pod","-l","app=order-management","-n",target,"--timeout=180s"],timeout=200)
        pod={"apiVersion":"v1","kind":"Pod","metadata":{"name":"schema-converter","namespace":target},"spec":{"restartPolicy":"Never","automountServiceAccountToken":False,"securityContext":{"runAsUser":10001,"runAsGroup":10001,"fsGroup":10001},"containers":[{"name":"convert","image":image,"imagePullPolicy":"IfNotPresent","command":["python","-m","kube_recover","convert","--database","/data/application.db","--target","2.0"],"volumeMounts":[{"name":"data","mountPath":"/data"}]}],"volumes":[{"name":"data","persistentVolumeClaim":{"claimName":"app-data"}}]}}
        dst.apply(pod)
        end=time.monotonic()+180
        while time.monotonic()<end:
            phase=dst.json(["get","pod","schema-converter","-n",target,"-o","json"])["status"]["phase"]
            if phase=="Succeeded":break
            if phase=="Failed":raise ValueError("Schema converter failed; inspect converter pod logs")
            time.sleep(1)
        else:raise ValueError("Schema conversion timed out")
        (out/"conversion.log").write_bytes(dst.run(["logs","schema-converter","-n",target]))
        result["plan"]=schema.compatibility("1.0","2.0")
        deployment=resources[2];deployment["metadata"]["namespace"]=target
        deployment["spec"]["template"]["spec"]["containers"][0]["command"]=["python","-m","kube_recover","workload","--version","2.0"]
        dst.apply(deployment)
        dst.run(["rollout","status","deployment/orders","-n",target,"--timeout=240s"],timeout=260)
        stage("verification","Checking the recovered v2 service and complete semantic state")
        with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        with (out/"port-forward.log").open("wb") as log:
            proc=subprocess.Popen(["kubectl","--context",target_context,"-n",target,"port-forward","service/orders",f"{port}:8080","--address=127.0.0.1"],stdout=log,stderr=log)
            try:
                base=f"http://127.0.0.1:{port}"
                for _ in range(100):
                    try:
                        with urlopen(base+"/state",timeout=2) as response:actual=json.load(response)
                        break
                    except OSError:time.sleep(.1)
                else:raise ValueError("Target service unavailable")
                errors=0;latencies=[]
                for _ in range(100):
                    start=time.perf_counter()
                    try:
                        with urlopen(base+"/orders",timeout=5) as response:rows=json.load(response)
                        if rows["application_version"]!="2.0" or rows["total"]!=expected["counts"]["orders"]:errors+=1
                    except (OSError,ValueError,KeyError):errors+=1
                    latencies.append((time.perf_counter()-start)*1000)
            finally:
                proc.terminate()
                try:proc.wait(timeout=10)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
        result.update(actual=actual,service_errors=errors,service_probes=100,service_latency_ms=latencies,
                      conversion_to_verified_ms=(time.perf_counter()-conversion_start)*1000,
                      checks={"schema":actual["application_version"]=="2.0","data":actual["semantic_sha256"]==expected["semantic_sha256"],"business":actual["total_minor"]==expected["total_minor"],"health":actual["healthy"],"service":errors==0})
        result["accepted"]=all(result["checks"].values())
        if not result["accepted"]:raise ValueError("Target application verification failed")
        stage("completed","Cross-cluster v1-to-v2 application recovery accepted")
        return result
    except Exception as e:
        result.update(accepted=False,error=str(e));stage("failed",str(e));raise
