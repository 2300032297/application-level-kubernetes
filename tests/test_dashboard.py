import json,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from kube_recover.console import Console
from kube_recover.recovery import Recovery
from kube_recover.pipeline import Pipeline
from kube_recover.storage import Storage,S3,endpoint

class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();Recovery(self.tmp.name).initialize(120)
        self.server=Console(('127.0.0.1',0),self.tmp.name)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_port}'
    def tearDown(self):self.server.shutdown();self.server.server_close();self.thread.join();self.tmp.cleanup()
    def get(self,path,headers=None):
        with urlopen(Request(self.url+path,headers=headers or {})) as response:return json.load(response)
    def post(self,path,data,token=True):
        headers={'Content-Type':'application/json'}
        if token:headers['X-CSRF-Token']=self.server.token
        with urlopen(Request(self.url+path,data=json.dumps(data).encode(),headers=headers)) as response:return json.load(response)
    def configure(self):
        return self.server.storage.save({'provider':'minio','endpoint':'http://127.0.0.1:9000','console_url':'http://127.0.0.1:9001','bucket':'test-bucket','region':'us-east-1','access_key':'TEST-LOCAL-ACCESS','secret_key':'TEST-PRIVATE-SECRET'})
    def test_secret_not_in_status_or_evidence(self):
        self.configure()
        for path in ['/api/status','/api/storage','/api/evidence','/api/pipeline/evidence']:
            body=json.dumps(self.get(path));self.assertNotIn('TEST-PRIVATE-SECRET',body);self.assertNotIn('TEST-LOCAL-ACCESS',body)
    def test_credentials_require_csrf(self):
        self.configure()
        with self.assertRaises(HTTPError) as error:self.post('/api/storage/credentials',{},False)
        self.assertEqual(error.exception.code,403)
    def test_credentials_explicit_reveal(self):
        self.configure();self.assertEqual(self.post('/api/storage/credentials',{})['secret_key'],'TEST-PRIVATE-SECRET')
    def test_rebinding_host_rejected(self):
        with self.assertRaises(HTTPError) as error:self.get('/api/status',{'Host':'attacker.example'})
        self.assertEqual(error.exception.code,403)
    def test_connection_preserves_saved_secrets_on_blank_fields(self):
        self.configure();self.post('/api/storage/config',{'bucket':'another-bucket','access_key':'','secret_key':''})
        self.assertEqual(self.server.storage.config()['secret_key'],'TEST-PRIVATE-SECRET')
    def test_invalid_endpoint_rejected(self):
        for value in ['file:///etc/passwd','http://key:secret@localhost:9000','https://example.test/path','http://localhost/?secret=1']:
            with self.assertRaises(ValueError):endpoint(value)
    def test_storage_config_cannot_change_during_job(self):
        self.server.job['busy']=True
        with self.assertRaises(HTTPError):self.post('/api/storage/config',{'bucket':'blocked-bucket'})
    def test_analytics_agrees_with_source(self):
        data=self.get('/api/analytics');self.assertEqual(sum(x['orders'] for x in data['statuses']),120)
        self.assertEqual(sum(x['orders'] for x in data['months']),120);self.assertTrue(data['synthetic'])
    def test_simulation_never_creates_backup_or_accepts_recovery(self):
        with patch('kube_recover.pipeline.time.sleep'):
            result=Pipeline(self.server).run({'mode':'simulation'})
        self.assertFalse(result['accepted']);self.assertTrue(result['simulated']);self.assertFalse(self.server.recovery.target.exists())
        self.assertEqual(self.server.recovery.backups(),[])
        self.assertTrue(all(x['status']=='simulated' for x in self.server.pipeline['steps']))
    def test_simulated_failure_is_explicitly_rejected(self):
        with patch('kube_recover.pipeline.time.sleep'):
            with self.assertRaises(ValueError):Pipeline(self.server).run({'mode':'simulation','scenario':'unsupported'})
        self.assertEqual(self.server.pipeline['status'],'failed');self.assertTrue(self.server.pipeline['simulated'])
    def test_real_local_pipeline_has_http_evidence_and_skips_s3(self):
        result=Pipeline(self.server).run({'mode':'local'})
        self.assertTrue(result['accepted']);self.assertFalse(result['simulated']);self.assertEqual(result['service']['requests'],12)
        steps={x['id']:x for x in self.server.pipeline['steps']};self.assertEqual(steps['upload']['status'],'skipped')
    def test_fault_pipeline_detects_and_repairs_real_target(self):
        result=Pipeline(self.server).run({'mode':'local','scenario':'data_fault'})
        self.assertTrue(result['accepted']);self.assertTrue(self.server.pipeline['fault_detected']);self.assertTrue(self.server.pipeline['fault_repaired'])
    def test_unknown_pipeline_mode_rejected(self):
        with self.assertRaises(ValueError):Pipeline(self.server).run({'mode':'pretend-cluster'})
    def test_storage_outage_cannot_silently_pass_in_local_mode(self):
        with self.assertRaisesRegex(ValueError,'storage mode'):Pipeline(self.server).run({'mode':'local','scenario':'storage_outage'})
        self.assertEqual(self.server.recovery.backups(),[])
    def test_unsupported_real_pipeline_preserves_previous_target(self):
        r=self.server.recovery;b=r.backup();before=r.recover(b['backup_id'])['actual']['semantic_sha256']
        with self.assertRaises(ValueError):Pipeline(self.server).run({'mode':'local','scenario':'unsupported'})
        self.assertEqual(r.status()['target']['semantic_sha256'],before)
    def test_unknown_get_does_not_reveal_credentials(self):
        self.configure()
        with self.assertRaises(HTTPError) as error:self.get('/api/storage/credentials')
        self.assertEqual(error.exception.code,404)
    def test_non_object_json_rejected(self):
        with self.assertRaises(HTTPError) as error:self.post('/api/storage/config',[])
        self.assertEqual(error.exception.code,400)

class RedirectTests(unittest.TestCase):
    def test_signed_client_never_follows_redirects(self):
        class Redirect(BaseHTTPRequestHandler):
            def do_GET(self):self.send_response(302);self.send_header('Location','http://127.0.0.1:1/private');self.end_headers()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Redirect);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=S3({'endpoint':f'http://127.0.0.1:{server.server_port}','bucket':'test-bucket','region':'us-east-1','access_key':'test','secret_key':'test'})
            with self.assertRaisesRegex(ValueError,'HTTP 302'):client.request('GET')
        finally:server.shutdown();server.server_close();thread.join()
