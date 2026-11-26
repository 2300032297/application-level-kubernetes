import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from kube_recover import schema
from kube_recover.recovery import Recovery,verify
from kube_recover.console import Console,Workload
from kube_recover.cluster import Kubectl,workload


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.r=Recovery(self.root);self.r.initialize(120)
    def tearDown(self):self.temp.cleanup()
    def test_seed_record_counts(self):
        s=schema.summary(self.r.source);self.assertEqual(s['counts']['orders'],120);self.assertGreater(s['counts']['order_items'],120)
    def test_deterministic_seed(self):
        p=self.root/'other.db';schema.seed(p,120);self.assertEqual(schema.summary(p)['semantic_sha256'],schema.summary(self.r.source)['semantic_sha256'])
    def test_seed_refuses_overwrite(self):
        with self.assertRaises(ValueError):schema.seed(self.r.source)
    def test_invalid_seed_size(self):
        for count in [0,-1,100001]:
            with self.assertRaises(ValueError):schema.seed(self.root/'bad.db',count)
    def test_version_upgrade_preserves_every_row(self):
        with schema.connect(self.r.source) as db:before=schema.canonical_rows(db)
        b=self.r.backup();result=self.r.recover(b['backup_id'])
        with schema.connect(self.r.target) as db:after=schema.canonical_rows(db)
        self.assertEqual(before,after);self.assertEqual(result['actual']['application_version'],'2.0');self.assertTrue(all(result['checks'].values()))
    def test_same_version_restore(self):
        b=self.r.backup();result=self.r.recover(b['backup_id'],'1.0');self.assertTrue(result['accepted']);self.assertEqual(result['plan']['converter'],'identity')
    def test_v2_to_v2(self):
        schema.convert(self.r.source,'2.0');b=self.r.backup();self.assertTrue(self.r.recover(b['backup_id'],'2.0')['accepted'])
    def test_downgrade_rejected(self):
        schema.convert(self.r.source,'2.0');b=self.r.backup()
        with self.assertRaisesRegex(ValueError,'Downgrade'):self.r.recover(b['backup_id'],'1.0')
        self.assertFalse(self.r.target.exists())
    def test_unknown_target_keeps_existing_target(self):
        b=self.r.backup();self.r.recover(b['backup_id']);before=self.r.target.read_bytes()
        with self.assertRaises(ValueError):self.r.recover(b['backup_id'],'3.0')
        self.assertEqual(before,self.r.target.read_bytes())
    def test_corrupt_artifact_rejected(self):
        b=self.r.backup();p,_=self.r.artifact(b['backup_id']);p.write_bytes(b'corruption')
        with self.assertRaisesRegex(ValueError,'checksum'):self.r.recover(b['backup_id'])
        self.assertFalse(self.r.target.exists())
    def test_manifest_drift_rejected(self):
        b=self.r.backup();p=self.root/'backups'/b['backup_id']/'manifest.json';m=json.loads(p.read_text());m['expected']['total_minor']+=1;p.write_text(json.dumps(m))
        with self.assertRaisesRegex(ValueError,'manifest'):self.r.recover(b['backup_id'])
    def test_backup_traversal_rejected(self):
        for key in ['../source','..','/tmp','abc/def','']:
            with self.assertRaises(ValueError):self.r.artifact(key)
    def test_bad_schema_fingerprint(self):
        with schema.connect(self.r.source,False) as db:db.execute('ALTER TABLE orders ADD COLUMN surprise TEXT')
        with self.assertRaisesRegex(ValueError,'fingerprint'):self.r.backup()
    def test_broken_customer_reference(self):
        db=sqlite3.connect(self.r.source);db.execute("UPDATE orders SET customer_email='missing@example.test' WHERE id=1");db.commit();db.close()
        with self.assertRaises(ValueError):self.r.backup()
    def test_business_fault_detected_and_recovered(self):
        b=self.r.backup();self.r.recover(b['backup_id']);self.r.corrupt_target();self.assertFalse(self.r.status()['target']['ready']);result=self.r.recover(b['backup_id']);self.assertTrue(result['accepted'])
    def test_idempotent_recovery(self):
        b=self.r.backup();a=self.r.recover(b['backup_id']);z=self.r.recover(b['backup_id']);self.assertEqual(a['actual']['semantic_sha256'],z['actual']['semantic_sha256'])
    def test_semantically_valid_drift_is_not_reported_as_verified(self):
        b=self.r.backup();self.r.recover(b['backup_id'])
        with schema.connect(self.r.target,False) as db:db.execute("UPDATE orders SET status='unexpected-status' WHERE id=1")
        self.assertFalse(self.r.status()['target']['ready'])
        self.assertTrue(self.r.recover(b['backup_id'])['accepted'])
    def test_source_unchanged(self):
        before=self.r.source.read_bytes();b=self.r.backup();self.r.recover(b['backup_id']);self.assertEqual(before,self.r.source.read_bytes())
    def test_search_and_pagination(self):
        a=schema.records(self.r.source,search='delivered');self.assertEqual(a['total'],24);self.assertTrue(all(r['status']=='delivered' for r in a['rows']))
        b=schema.records(self.r.source,page=2);self.assertEqual(b['rows'][0]['id'],21)
    def test_unknown_table_rejected(self):
        with self.assertRaises(ValueError):schema.records(self.r.source,'orders; DROP TABLE orders')
    def test_unknown_schema_rejected(self):
        with schema.connect(self.r.source,False) as db:db.execute('PRAGMA user_version=9')
        with self.assertRaises(ValueError):self.r.backup()
    def test_tampered_currency_rejected(self):
        schema.convert(self.r.source,'2.0');db=sqlite3.connect(self.r.source);db.execute('PRAGMA ignore_check_constraints=ON');db.execute("UPDATE orders SET currency='USD' WHERE id=1");db.commit();db.close()
        with self.assertRaises(ValueError):schema.summary(self.r.source)
    def test_failed_conversion_transaction_rolls_back(self):
        db=sqlite3.connect(self.r.source);db.execute('CREATE TABLE orders_v2(id INTEGER)');db.commit();db.close()
        with self.assertRaises(sqlite3.Error):schema.convert(self.r.source,'2.0')
        self.assertEqual(schema.summary(self.r.source)['application_version'],'1.0')
    def test_cluster_context_required(self):
        with self.assertRaises(ValueError):Kubectl('')
    def test_cluster_manifest_uses_versioned_service(self):
        r=workload('test','kr-local','image:1',100);self.assertEqual(len(r),4);self.assertEqual(r[2]['spec']['template']['metadata']['annotations']['backup.velero.io/backup-volumes'],'data')


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();r=Recovery(cls.temp.name);r.initialize(120)
        cls.server=Console(('127.0.0.1',0),cls.temp.name);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start();cls.url=f'http://127.0.0.1:{cls.server.server_port}'
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join();cls.temp.cleanup()
    def get(self,path):
        with urlopen(self.url+path) as r:return json.load(r)
    def test_console_serves_html(self):
        with urlopen(self.url) as r:self.assertIn(b'Recovery workspace',r.read())
    def test_api_pagination(self):self.assertEqual(self.get('/api/records?page=2')['rows'][0]['id'],21)
    def test_csrf_required(self):
        req=Request(self.url+'/api/action',data=b'{"action":"demo"}',headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as e:urlopen(req)
        self.assertEqual(e.exception.code,403)
    def test_unknown_role_rejected(self):
        with self.assertRaises(HTTPError) as e:self.get('/api/records?role=../../../etc')
        self.assertEqual(e.exception.code,400)
    def test_cross_origin_rejected(self):
        token=self.get('/api/status')['csrf'];req=Request(self.url+'/api/action',data=b'{"action":"demo"}',headers={'X-CSRF-Token':token,'Origin':'http://other.test'})
        with self.assertRaises(HTTPError) as e:urlopen(req)
        self.assertEqual(e.exception.code,403)
    def test_http_full_recovery(self):
        token=self.get('/api/status')['csrf'];req=Request(self.url+'/api/action',data=b'{"action":"demo"}',headers={'X-CSRF-Token':token})
        with urlopen(req) as response:self.assertEqual(response.status,202)
        for _ in range(200):
            s=self.get('/api/status')
            if not s['job']['busy']:break
            time.sleep(.02)
        self.assertTrue(s['job']['result']['accepted']);self.assertEqual(self.get('/api/records?role=target')['application_version'],'2.0')
    def test_export_csv(self):
        with urlopen(self.url+'/api/export?table=customers') as r:self.assertIn(b'example.test',r.read());self.assertIn('text/csv',r.headers['Content-Type'])
    def test_workload_rejects_wrong_version(self):
        app=Workload(('127.0.0.1',0),self.server.recovery.source,'2.0');t=threading.Thread(target=app.serve_forever,daemon=True);t.start()
        try:
            for endpoint in ['/healthz','/orders']:
                with self.assertRaises(HTTPError) as e:urlopen(f'http://127.0.0.1:{app.server_port}'+endpoint)
                self.assertEqual(e.exception.code,503)
        finally:app.shutdown();app.server_close();t.join()

if __name__=='__main__':unittest.main()
