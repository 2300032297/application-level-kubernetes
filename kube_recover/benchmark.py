"""Repeatable local experiments; results are generated, never embedded."""
from pathlib import Path
import platform
import sqlite3
import statistics
import time
import threading
from urllib.request import urlopen
import json
from .console import Workload
from .recovery import Recovery
from .util import distribution,write_json


def evaluate(output,trials=30,orders=5000):
    if trials<1:raise ValueError('Positive trials required')
    out=Path(output)
    if out.exists() and any(out.iterdir()):raise ValueError('Use a new output directory')
    out.mkdir(parents=True,exist_ok=True)
    r=Recovery(out/'state');r.initialize(orders)
    rows=[]
    for i in range(trials):
        b=r.backup()
        a=r.recover(b['backup_id'],'1.0');z=r.recover(b['backup_id'],'2.0')
        # A naive v2 process on unchanged v1 schema fails its real HTTP health check.
        server=Workload(('127.0.0.1',0),r.source,'2.0');t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);t.start()
        try:
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/healthz') as response:naive=response.status==200
            except OSError:naive=False
        finally:server.shutdown();server.server_close();t.join()
        r.corrupt_target();detected=not r.status()['target']['ready'];repaired=r.recover(b['backup_id'],'2.0')
        rows.append({'trial':i+1,'backup_ms':b['backup_ms'],'same_version_ms':a['recovery_ms'],'cross_version_ms':z['recovery_ms'],
                     'same_version_accepted':a['accepted'],'cross_version_accepted':z['accepted'],'naive_accepted':naive,'corruption_detected':detected,'repair_accepted':repaired['accepted'],'repair_ms':repaired['recovery_ms']})
    server=Workload(('127.0.0.1',0),r.target,'2.0');t=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01},daemon=True);t.start();times=[];errors=0
    try:
        for _ in range(1000):
            start=time.perf_counter()
            try:
                with urlopen(f'http://127.0.0.1:{server.server_port}/orders') as response:data=json.load(response)
                if data['total']!=orders or data['application_version']!='2.0':errors+=1
            except (OSError,ValueError):errors+=1
            times.append((time.perf_counter()-start)*1000)
    finally:server.shutdown();server.server_close();t.join()
    result={'environment':{'platform':platform.platform(),'python':platform.python_version(),'sqlite':sqlite3.sqlite_version},'trials':trials,'orders':orders,'scope':'local SQLite, filesystem and loopback HTTP; no Kubernetes timing',
            'success':{key:sum(int(row[key]) for row in rows) for key in ('same_version_accepted','cross_version_accepted','naive_accepted','corruption_detected','repair_accepted')},
            'timings_ms':{key:{**distribution([r[key] for r in rows]),'mean':statistics.mean(r[key] for r in rows)} for key in ('backup_ms','same_version_ms','cross_version_ms','repair_ms')},
            'service':{'probes':1000,'errors':errors,'latency_ms':distribution(times)},'dataset':r.status()['source']}
    write_json(out/'summary.json',result)
    (out/'trials.jsonl').write_text('\n'.join(json.dumps(row) for row in rows)+'\n')
    write_json(out/'service-latencies.json',times)
    return result
