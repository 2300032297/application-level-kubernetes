from __future__ import annotations
import argparse
import json
from pathlib import Path
from . import schema
from .console import Console,Workload
from .recovery import Recovery


def main():
    p=argparse.ArgumentParser(description="Application-Level Kubernetes Backup, Restore and Migration")
    sub=p.add_subparsers(dest="command",required=True)
    c=sub.add_parser("console");c.add_argument("--root",default="runtime");c.add_argument("--host",default="127.0.0.1");c.add_argument("--port",type=int,default=8090)
    c=sub.add_parser("seed");c.add_argument("--output",required=True);c.add_argument("--orders",type=int,default=5000);c.add_argument("--seed",type=int,default=101)
    c=sub.add_parser("inspect");c.add_argument("--database",required=True)
    c=sub.add_parser("convert");c.add_argument("--database",required=True);c.add_argument("--target",default="2.0")
    c=sub.add_parser("workload");c.add_argument("--database",default="/data/application.db");c.add_argument("--version",choices=["1.0","2.0"],required=True);c.add_argument("--host",default="0.0.0.0");c.add_argument("--port",type=int,default=8080)
    c=sub.add_parser("demo");c.add_argument("--root",default="runtime");c.add_argument("--orders",type=int,default=5000)
    c=sub.add_parser("cluster");
    for key in ("source-context","target-context"):c.add_argument("--"+key,required=True)
    for key in ("source-class","target-class"):c.add_argument("--"+key,default="kr-local")
    c.add_argument("--image",default="kube-recover:1.0.0");c.add_argument("--output",required=True);c.add_argument("--orders",type=int,default=5000)
    c=sub.add_parser("benchmark");c.add_argument("--output",required=True);c.add_argument("--trials",type=int,default=30);c.add_argument("--orders",type=int,default=5000)
    a=p.parse_args()
    try:
        if a.command=="console":
            server=Console((a.host,a.port),a.root)
            print(f"Recovery console: http://{a.host}:{server.server_port}",flush=True)
            try:server.serve_forever()
            finally:server.server_close()
            return
        if a.command=="workload":
            server=Workload((a.host,a.port),a.database,a.version)
            try:server.serve_forever()
            finally:server.server_close()
            return
        if a.command=="seed":result=schema.seed(a.output,a.orders,a.seed)
        elif a.command=="inspect":result=schema.summary(a.database)
        elif a.command=="convert":result=schema.convert(a.database,a.target)
        elif a.command=="demo":
            r=Recovery(a.root);r.initialize(a.orders);b=r.backup();result=r.recover(b["backup_id"])
        elif a.command=="cluster":
            from .cluster import run_cluster
            result=run_cluster(a.source_context,a.target_context,a.source_class,a.target_class,a.image,a.output,a.orders)
        elif a.command=="benchmark":
            from .benchmark import evaluate
            result=evaluate(a.output,a.trials,a.orders)
        print(json.dumps(result,indent=2))
    except Exception as e:
        p.exit(2,f"Recovery rejected: {e}\n")

if __name__=="__main__":main()
