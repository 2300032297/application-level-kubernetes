"""Verified immutable backups, staged schema recovery, and semantic acceptance."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from . import schema
from .util import digest, file_hash, read_json, write_json

TITLE = "Application-Level Kubernetes Backup, Restore and Migration"
ABSTRACT = """Kubernetes is widely used to deploy and manage containerized applications. As organizations increasingly rely on Kubernetes, ensuring reliable backup, restore, and migration of applications has become essential for preventing data loss and reducing downtime. Existing tools such as Velero provide backup and restore capabilities, but they work effectively only when the source and target Kubernetes environments use compatible application versions. Restoring applications across different versions can lead to compatibility issues or unsuccessful recovery.

This project proposes an Application-Level Kubernetes Backup, Restore, and Migration system that addresses this limitation. The system extends the backup and restore process by supporting application recovery across different versions through compatibility checking and validation. It verifies whether the restored application functions correctly after recovery and helps simplify migration between Kubernetes clusters. The project uses Kubernetes, Docker, Velero, kubectl, and cloud storage to provide a reliable backup and recovery solution. The main objective is to improve application availability, reduce downtime, and make backup and migration more dependable even when application versions differ."""


def verify(expected, path, target_version):
    actual = schema.summary(path)
    checks = {"schema": actual["application_version"] == target_version,
              "data": actual["semantic_sha256"] == expected["semantic_sha256"] and actual["counts"] == expected["counts"],
              "business": actual["total_minor"] == expected["total_minor"] and actual["currency"] == expected["currency"],
              "health": actual["integrity"] == "ok"}
    return {"accepted":all(checks.values()),"checks":checks,"actual":actual}


class Recovery:
    def __init__(self, root):
        self.root = Path(root).resolve()
        for folder in ("source","target","backups","runs"):
            (self.root/folder).mkdir(parents=True,exist_ok=True)
        self.source = self.root/"source/application.db"
        self.target = self.root/"target/application.db"

    def initialize(self, count=None, seed=101):
        if not self.source.exists():
            bundled=Path(__file__).parent.parent/'data/orders-v1.db'
            if count is None and bundled.exists():return schema.snapshot(bundled,self.source)
            return schema.seed(self.source,count or 25000,seed)
        return schema.summary(self.source)

    def backups(self):
        result = []
        for p in sorted((self.root/"backups").glob("*/manifest.json"),reverse=True):
            try:
                m = read_json(p)
                result.append({"id":p.parent.name, "created_at":m["created_at"], "source":m["expected"], "sha256":m["sha256"]})
            except (OSError,ValueError,KeyError):
                continue
        return result

    def backup(self, event=lambda *_:None):
        start = time.perf_counter()
        key = time.strftime("%Y%m%d-%H%M%S",time.gmtime())+"-"+uuid.uuid4().hex[:8]
        temporary = self.root/"backups"/(".pending-"+key)
        final = self.root/"backups"/key
        temporary.mkdir()
        try:
            event("backup","Taking a transaction-consistent SQLite backup")
            expected = schema.snapshot(self.source,temporary/"application.db")
            manifest = {"format":1,"id":key,"created_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                        "expected":expected,"sha256":file_hash(temporary/"application.db"),"engine":"sqlite-online-backup"}
            manifest["manifest_hash"] = digest(manifest)
            write_json(temporary/"manifest.json",manifest)
            os.replace(temporary,final)
            return {"backup_id":key,"backup_ms":(time.perf_counter()-start)*1000,"manifest":manifest}
        finally:
            if temporary.exists(): shutil.rmtree(temporary)

    def artifact(self, key):
        if not key or any(c not in "0123456789-abcdef" for c in key):
            raise ValueError("Invalid backup identifier")
        folder = self.root/"backups"/key
        m = read_json(folder/"manifest.json")
        if m.get("manifest_hash") != digest({k:v for k,v in m.items() if k!="manifest_hash"}):
            raise ValueError("Backup manifest integrity failed")
        if m.get("id") != key or file_hash(folder/"application.db") != m.get("sha256"):
            raise ValueError("Backup checksum failed; target was not changed")
        return folder/"application.db",m

    def recover(self, key, target_version="2.0", event=lambda *_:None):
        start = time.perf_counter()
        source,m = self.artifact(key)
        event("compatibility","Checking source schema and approved version conversion")
        before = schema.summary(source)
        if before["semantic_sha256"] != m["expected"]["semantic_sha256"]:
            raise ValueError("Backup semantic manifest mismatch")
        plan = schema.compatibility(before["application_version"],target_version)
        fd,temp = tempfile.mkstemp(prefix=".recover-",suffix=".db",dir=self.target.parent)
        os.close(fd)
        staged = Path(temp)
        try:
            event("restore","Restoring verified backup into isolated staging")
            shutil.copyfile(source,staged)
            event("conversion", "Applying "+plan["converter"])
            schema.convert(staged,target_version)
            event("verification","Checking every record, foreign key, order total and schema")
            oracle = verify(m["expected"],staged,target_version)
            if not oracle["accepted"]:
                raise ValueError("Application verification rejected recovery")
            with staged.open("r+b") as f: f.flush();os.fsync(f.fileno())
            event("publish","Publishing the independently verified target")
            # Windows readers can briefly hold a SQLite handle without delete
            # sharing. Retry the atomic rename, never modify the live file.
            for attempt in range(40):
                try:os.replace(staged,self.target);break
                except PermissionError:
                    if attempt==39:raise
                    time.sleep(.05)
            result = {"accepted":True,"backup_id":key,"plan":plan,**oracle,
                      "recovery_ms":(time.perf_counter()-start)*1000,"mode":"local-sqlite","finished_at":time.time()}
            write_json(self.root/"runs"/(uuid.uuid4().hex+".json"),result)
            return result
        finally:
            staged.unlink(missing_ok=True)

    def corrupt_target(self):
        if not self.target.exists(): raise ValueError("Recover a target before injecting a fault")
        # Controlled business-data corruption; only the project's generated target.
        with schema.connect(self.target,False) as db:
            version = schema.inspect(db)
            col = "total_cents" if version == "1.0" else "total_minor"
            db.execute(f"UPDATE orders SET {col}={col}+1 WHERE id=(SELECT MIN(id) FROM orders)")
        return {"fault":"order-total-corruption","target_healthy":False}

    def status(self):
        def state(p):
            try: return {"ready":True,**schema.summary(p)}
            except Exception as e: return {"ready":False,"error":str(e)}
        runs=[]
        for p in sorted((self.root/"runs").glob("*.json"),key=lambda p:p.stat().st_mtime,reverse=True)[:30]:
            try: runs.append(read_json(p))
            except (ValueError,OSError): pass
        target=state(self.target)
        if target.get('ready') and runs:
            last=runs[0]['actual']
            if target['semantic_sha256']!=last['semantic_sha256'] or target['application_version']!=last['application_version']:
                target.update(ready=False,error='Target drift differs from the last accepted recovery')
        return {"title":TITLE,"source":state(self.source),"target":target,"backups":self.backups(),"runs":runs}
