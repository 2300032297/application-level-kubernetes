"""Loopback recovery dashboard and versioned workload HTTP service."""
from __future__ import annotations
import csv
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import secrets
import sqlite3
import threading
import time
import os
import tempfile
from urllib.parse import parse_qs,urlsplit
from . import schema
from .recovery import Recovery,TITLE,ABSTRACT
from .storage import Storage


class Console(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,root):
        self.recovery=Recovery(root)
        self.recovery.initialize()
        self.token=secrets.token_urlsafe(32)
        self.storage=Storage(self.recovery.root)
        self.pipeline={"status":"idle","steps":[],"simulated":True}
        self.cache={};self.cache_time=0
        self.mutex=threading.Lock()
        self.events=[]
        self.job={"busy":False,"phase":"ready","message":"Ready to protect the source application"}
        super().__init__(address,Handler)

    def event(self,phase,message):
        self.job={**self.job,"phase":phase,"message":message}
        self.events.append({"time":time.time(),"phase":phase,"message":message})
        self.events=self.events[-80:]

    def submit(self,action,params):
        if action not in {"backup","recover","demo","corrupt","cluster","pipeline","storage_test","storage_upload","seed"}: raise ValueError("Unknown action")
        if not self.mutex.acquire(False): raise ValueError("Another operation is running")
        self.job={"busy":True,"phase":"starting","message":"Starting operation","action":action}
        def run():
            try:
                r=self.recovery
                if action=="backup": result=r.backup(self.event)
                elif action=="corrupt": result=r.corrupt_target()
                elif action=="storage_test": result=self.storage.test()
                elif action=="storage_upload":
                    key=params.get('backup_id') or (r.backups()[0]['id'] if r.backups() else '')
                    result=self.storage.upload(r,key)
                elif action=="pipeline":
                    from .pipeline import Pipeline
                    result=Pipeline(self).run(params)
                elif action=="seed":
                    count=int(params.get('orders',50000))
                    self.event('dataset',f'Generating {count:,} synthetic orders; existing backups and target are retained')
                    fd,name=tempfile.mkstemp(prefix='.dataset-',suffix='.db',dir=r.source.parent);os.close(fd);Path(name).unlink()
                    try:result=schema.seed(name,count,int(params.get('seed',101)));os.replace(name,r.source)
                    finally:Path(name).unlink(missing_ok=True)
                elif action=="cluster":
                    from .cluster import run_cluster
                    allowed={k:params[k] for k in ("source_context","target_context","source_class","target_class","image") if k in params}
                    result=run_cluster(output=r.root/"cluster-runs"/str(int(time.time())),event=self.event,**allowed)
                else:
                    key=params.get("backup_id")
                    if action=="demo" or not key: key=r.backup(self.event)["backup_id"]
                    result=r.recover(key,params.get("target_version","2.0"),self.event)
                message='Simulation completed; no recovery was executed' if result.get('simulated') else 'Operation completed'
                self.job={"busy":False,"phase":"completed","message":message,"action":action,"result":result}
            except Exception as e:
                self.event("failed",str(e))
                self.job={**self.job,"busy":False,"phase":"failed","error":str(e)}
            finally: self.mutex.release()
        threading.Thread(target=run,daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def reply(self,value,status=200,content="application/json; charset=utf-8",extra=None):
        data=value if isinstance(value,bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type",content)
        self.send_header("Content-Length",str(len(data)))
        self.send_header("Cache-Control","no-store")
        self.send_header("X-Content-Type-Options","nosniff")
        self.send_header("Content-Security-Policy","default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'")
        for k,v in (extra or {}).items(): self.send_header(k,v)
        self.end_headers();self.wfile.write(data)

    def do_GET(self):
        if hasattr(self.server,'token') and urlsplit('http://'+self.headers.get('Host','')).hostname not in {'127.0.0.1','localhost','::1'}:
            self.reply({'error':'Use the local console URL'},403);return
        u=urlsplit(self.path);q=parse_qs(u.query)
        arg=lambda k,d="":q.get(k,[d])[0]
        try:
            if u.path=="/": self.reply(Path(__file__).with_name("dashboard.html").read_bytes(),content="text/html; charset=utf-8")
            elif u.path=="/api/status":
                if time.monotonic()-self.server.cache_time>2:
                    self.server.cache=self.server.recovery.status();self.server.cache_time=time.monotonic()
                self.reply({**self.server.cache,"csrf":self.server.token,"job":self.server.job,"events":self.server.events,"pipeline":self.server.pipeline,"storage":self.server.storage.public()})
            elif u.path=="/api/storage":self.reply(self.server.storage.public())
            elif u.path=="/api/storage/objects":self.reply(self.server.storage.client().objects(arg('prefix'),arg('token')))
            elif u.path=="/api/storage/download":
                key=arg('key')
                if not key or len(key)>2000:raise ValueError('Choose an object key')
                self.reply(self.server.storage.client().request('GET',key),content='application/octet-stream',extra={'Content-Disposition':'attachment; filename=storage-object.bin'})
            elif u.path=="/api/analytics":
                with schema.connect(self.server.recovery.source) as db:
                    version=schema.inspect(db);amount='total_cents' if version=='1.0' else 'total_minor'
                    statuses=[dict(r) for r in db.execute(f'SELECT status,COUNT(*) AS orders,SUM({amount}) AS total_minor FROM orders GROUP BY status ORDER BY orders DESC')]
                    months=[dict(r) for r in db.execute(f'SELECT SUBSTR(created_at,1,7) AS month,COUNT(*) AS orders,SUM({amount}) AS total_minor FROM orders GROUP BY month ORDER BY month')]
                    cities=[dict(r) for r in db.execute('SELECT city,COUNT(*) AS customers FROM customers GROUP BY city ORDER BY customers DESC')]
                self.reply({'statuses':statuses,'months':months,'cities':cities,'synthetic':True})
            elif u.path=="/api/pipeline/evidence":
                self.reply(self.server.pipeline,extra={"Content-Disposition":"attachment; filename=pipeline-evidence.json"})
            elif u.path=="/api/abstract": self.reply({"title":TITLE,"abstract":ABSTRACT})
            elif u.path=="/healthz": self.reply({"healthy":True})
            elif u.path=="/api/records":
                role=arg("role","source")
                if role not in {"source","target"}: raise ValueError("Unknown role")
                p=getattr(self.server.recovery,role)
                self.reply(schema.records(p,arg("table","orders"),arg("search"),int(arg("page","1"))))
            elif u.path=="/api/evidence":
                self.reply(self.server.recovery.status(),extra={"Content-Disposition":"attachment; filename=recovery-evidence.json"})
            elif u.path=="/api/export":
                role=arg("role","source");table=arg("table","orders")
                if role not in {"source","target"} or table not in {"orders","customers","products","order_items"}: raise ValueError("Unknown dataset")
                with schema.connect(getattr(self.server.recovery,role)) as db:
                    rows=schema.canonical_rows(db)[table]
                stream=io.StringIO();w=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else ["id"]);w.writeheader();w.writerows(rows)
                self.reply(stream.getvalue().encode(),content="text/csv; charset=utf-8",extra={"Content-Disposition":f"attachment; filename={role}-{table}.csv"})
            else: self.reply({"error":"Not found"},404)
        except (ValueError,OSError,sqlite3.Error) as e:self.reply({"error":str(e)},400)

    def do_POST(self):
        try:
            if urlsplit('http://'+self.headers.get('Host','')).hostname not in {'127.0.0.1','localhost','::1'}:
                self.reply({'error':'Use the local console URL'},403);return
            if self.path not in {"/api/action","/api/storage/config","/api/storage/credentials"}: self.reply({"error":"Not found"},404);return
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token",""),self.server.token): self.reply({"error":"Refresh the console before submitting"},403);return
            origin=self.headers.get("Origin")
            if origin and urlsplit(origin).netloc!=self.headers.get("Host"): self.reply({"error":"Invalid origin"},403);return
            length=int(self.headers.get("Content-Length","0"))
            if length<1 or length>16000: raise ValueError("Invalid request size")
            body=json.loads(self.rfile.read(length))
            if not isinstance(body,dict):raise ValueError('Expected a JSON object')
            if self.path=='/api/storage/credentials':
                cfg=self.server.storage.config();self.reply({'access_key':cfg.get('access_key',''),'secret_key':cfg.get('secret_key','')});return
            if self.path=='/api/storage/config':
                if self.server.job['busy']:raise ValueError('Wait for the active operation before changing storage')
                self.reply(self.server.storage.save(body));return
            self.server.submit(body.get("action"),body)
            self.reply({"queued":True},202)
        except (ValueError,KeyError,TypeError) as e:self.reply({"error":str(e)},400)


class Workload(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,database,version):
        self.database=Path(database);self.version=version
        super().__init__(address,WorkloadHandler)


class WorkloadHandler(Handler):
    def do_GET(self):
        try:
            u=urlsplit(self.path)
            if u.path in {"/healthz","/state"}:
                s=schema.summary(self.server.database)
                healthy=s["application_version"]==self.server.version
                self.reply({"healthy":healthy,**s},200 if healthy else 503)
            elif u.path=="/orders":
                rows=schema.records(self.server.database,"orders",page=int(parse_qs(u.query).get("page",["1"])[0]))
                if rows['application_version']!=self.server.version:
                    self.reply({'error':'Application version does not match the restored database schema','expected_version':self.server.version,'actual_version':rows['application_version']},503)
                else:self.reply(rows)
            else:self.reply({"application":"order-management","version":self.server.version})
        except Exception as e:self.reply({"healthy":False,"error":str(e)},503)
    def do_POST(self):self.reply({"error":"Workload is read-only during recovery validation"},405)
