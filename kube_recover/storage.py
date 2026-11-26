"""Small signed S3 client and private local connection configuration."""
from __future__ import annotations
import configparser
from datetime import datetime,timezone
import hashlib,hmac,json,os,re,secrets,uuid
from pathlib import Path
from urllib.error import HTTPError,URLError
from urllib.parse import quote,urlsplit,urlencode
from urllib.request import Request,urlopen,build_opener,HTTPRedirectHandler
import xml.etree.ElementTree as ET

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def endpoint(value):
    u=urlsplit(str(value))
    if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in {'','/'}:
        raise ValueError('Enter an HTTP(S) storage endpoint without a path or embedded credentials')
    if any(c in value for c in '\r\n\t'):raise ValueError('Invalid endpoint')
    return value.rstrip('/')

class S3:
    def __init__(self,config):
        self.config=config;self.url=endpoint(config['endpoint']);self.bucket=config['bucket']
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',self.bucket):raise ValueError('Invalid S3 bucket name')
        self.opener=build_opener(NoRedirect())
    def request(self,method,key=None,body=b'',query=None):
        cfg=self.config;host=urlsplit(self.url).netloc;now=datetime.now(timezone.utc)
        day=now.strftime('%Y%m%d');date=now.strftime('%Y%m%dT%H%M%SZ');region=cfg.get('region','us-east-1')
        path='/'+self.bucket+('/'+key if key is not None else '')
        uri=quote(path,safe='/~');query_string='&'.join(quote(str(k),safe='~')+'='+quote(str(v),safe='~') for k,v in sorted((query or {}).items()))
        payload=hashlib.sha256(body).hexdigest();names='host;x-amz-content-sha256;x-amz-date'
        headers=f'host:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{date}\n'
        canonical='\n'.join([method,uri,query_string,headers,names,payload]);scope=f'{day}/{region}/s3/aws4_request'
        signing=('AWS4'+cfg['secret_key']).encode()
        for part in [day,region,'s3','aws4_request']:signing=hmac.new(signing,part.encode(),hashlib.sha256).digest()
        message='AWS4-HMAC-SHA256\n'+date+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
        signature=hmac.new(signing,message.encode(),hashlib.sha256).hexdigest()
        auth=f'AWS4-HMAC-SHA256 Credential={cfg["access_key"]}/{scope}, SignedHeaders={names}, Signature={signature}'
        req=Request(self.url+uri+('?'+query_string if query_string else ''),method=method,data=body if method=='PUT' else None,
                    headers={'Host':host,'X-Amz-Date':date,'X-Amz-Content-Sha256':payload,'Authorization':auth})
        try:
            with self.opener.open(req,timeout=15) as r:
                data=r.read(64*1024*1024+1)
                if len(data)>64*1024*1024:raise ValueError('Object exceeds the 64 MB console transfer limit')
                return data
        except HTTPError as e:
            raise StorageError(e.code,f'Storage returned HTTP {e.code}; check endpoint, bucket and credentials') from None
        except (URLError,TimeoutError,OSError):raise ValueError('Storage is unreachable; start the service and check the endpoint') from None
    def ensure_bucket(self):
        try:self.request('GET',query={'list-type':'2','max-keys':'1'})
        except StorageError as e:
            if e.code!=404:raise
            region=self.config.get('region','us-east-1')
            body=b'' if region=='us-east-1' else ('<CreateBucketConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><LocationConstraint>'+region+'</LocationConstraint></CreateBucketConfiguration>').encode()
            self.request('PUT',body=body)
    def objects(self,prefix='',token=''):
        query={'list-type':'2','max-keys':'100','prefix':prefix}
        if token:query['continuation-token']=token
        root=ET.fromstring(self.request('GET',query=query));ns={'s':'http://s3.amazonaws.com/doc/2006-03-01/'}
        rows=[]
        for obj in root.findall('s:Contents',ns):
            rows.append({'key':obj.findtext('s:Key','',ns),'size':int(obj.findtext('s:Size','0',ns)),
                         'modified':obj.findtext('s:LastModified','',ns),'etag':obj.findtext('s:ETag','',ns).strip('"')})
        return {'objects':rows,'count':len(rows),'page_bytes':sum(x['size'] for x in rows),
                'next_token':root.findtext('s:NextContinuationToken','',ns),'truncated':root.findtext('s:IsTruncated','false',ns)=='true','bucket':self.bucket}
    def probe(self):
        self.ensure_bucket();key='applications/.checks/'+uuid.uuid4().hex+'.txt';body=b'Kube Recover signed storage verification'
        self.request('PUT',key,body)
        try:
            if self.request('GET',key)!=body:raise ValueError('Storage returned different bytes')
        finally:self.request('DELETE',key)
        return {'accepted':True,'checks':['signed-put','exact-get','signed-delete'],'checked_at':time_now()}

class StorageError(ValueError):
    def __init__(self,code,message):super().__init__(message);self.code=code

def time_now():return datetime.now(timezone.utc).isoformat()

class Storage:
    def __init__(self,root):self.root=Path(root);self.path=self.root/'storage.json';self.check={}
    def config(self):
        if self.path.exists():return json.loads(self.path.read_text(encoding='utf-8-sig'))
        # A host console can also inspect its separately configured Velero lab repository.
        credentials=self.root/'lab/credentials'
        if credentials.exists():
            c=configparser.ConfigParser();c.read(credentials,encoding='utf-8-sig')
            return {'provider':'seaweedfs','endpoint':'http://127.0.0.1:9090','console_url':'http://127.0.0.1:9091/buckets/velero/','bucket':'velero','region':'us-east-1','access_key':c['default']['aws_access_key_id'],'secret_key':c['default']['aws_secret_access_key']}
        return {'provider':'minio','endpoint':'http://127.0.0.1:9000','console_url':'http://127.0.0.1:9001','bucket':'application-backups','region':'us-east-1','access_key':'','secret_key':''}
    def public(self):
        cfg=self.config()
        return {k:v for k,v in cfg.items() if k not in {'access_key','secret_key'}}|{'configured':bool(cfg.get('secret_key') and cfg.get('access_key')),'access_key_hint':cfg.get('access_key','')[:3]+'••••' if cfg.get('access_key') else '', 'secret_saved':bool(cfg.get('secret_key')),'check':self.check}
    def save(self,data):
        old=self.config();cfg={k:str(data.get(k,old.get(k,''))).strip() for k in ['provider','endpoint','console_url','bucket','region','access_key','secret_key']}
        for k in ['access_key','secret_key']:
            if not cfg[k]:cfg[k]=old.get(k,'')
        cfg['endpoint']=endpoint(cfg['endpoint'])
        if cfg['console_url']:
            u=urlsplit(cfg['console_url'])
            if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password:raise ValueError('Invalid console URL')
        if cfg['provider'] not in {'minio','seaweedfs','s3'}:raise ValueError('Choose a supported storage provider')
        if not re.fullmatch(r'[a-z0-9-]{1,50}',cfg['region']):raise ValueError('Invalid storage region')
        if not cfg['access_key'] or not cfg['secret_key']:raise ValueError('Access key and secret key are required')
        S3(cfg);self.root.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_suffix('.tmp');tmp.write_text(json.dumps(cfg,indent=2),encoding='utf-8');os.chmod(tmp,0o600);os.replace(tmp,self.path);self.check={}
        return self.public()
    def client(self):
        cfg=self.config()
        if not cfg.get('secret_key'):raise ValueError('Configure storage credentials first')
        return S3(cfg)
    def test(self):self.check=self.client().probe();return self.check
    def upload(self,recovery,key):
        db,manifest=recovery.artifact(key);client=self.client();client.ensure_bucket();prefix='applications/'+key+'/'
        files=[('application.db',db.read_bytes()),('manifest.json',json.dumps(manifest,sort_keys=True).encode())]
        for name,content in files:
            client.request('PUT',prefix+name,content)
            if client.request('GET',prefix+name)!=content:raise ValueError('Uploaded backup failed exact read-back verification')
        return {'accepted':True,'backup_id':key,'prefix':prefix,'objects':2,'bytes':sum(len(b) for _,b in files),'provider':self.config()['provider'],'verified_at':time_now()}
    def fetch_backup(self,recovery,key):
        # Always compare remote bytes to the separately retained original manifest.
        _,expected=recovery.artifact(key);remote=self.client().request('GET','applications/'+key+'/application.db')
        if hashlib.sha256(remote).hexdigest()!=expected['sha256']:raise ValueError('Remote backup checksum differs from protected source')
        remote_manifest=json.loads(self.client().request('GET','applications/'+key+'/manifest.json'))
        if remote_manifest!=expected:raise ValueError('Remote backup manifest differs from protected source')
        from .util import digest
        imported=key+'-'+uuid.uuid4().hex[:8]
        folder=recovery.root/'backups'/('.pending-'+imported);folder.mkdir()
        try:
            manifest={**expected,'id':imported,'remote_source':{'backup_id':key,'bucket':self.config()['bucket'],'provider':self.config()['provider']}}
            manifest.pop('manifest_hash',None);manifest['manifest_hash']=digest(manifest)
            (folder/'application.db').write_bytes(remote)
            (folder/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
            os.replace(folder,recovery.root/'backups'/imported)
        finally:
            if folder.exists():
                import shutil
                shutil.rmtree(folder)
        return {'accepted':True,'backup_id':imported,'source_backup_id':key,'bytes':len(remote),'sha256':expected['sha256']}
