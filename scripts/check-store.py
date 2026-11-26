"""Exercise the lab S3 API with signed PUT/GET/DELETE using standard Python."""
import argparse
import configparser
from datetime import datetime,timezone
import hashlib
import hmac
import json
from pathlib import Path
from urllib.parse import urlsplit,quote
from urllib.request import Request,urlopen
import uuid


def request(method,endpoint,path,access,secret,body=b''):
    host=urlsplit(endpoint).netloc
    now=datetime.now(timezone.utc)
    day=now.strftime('%Y%m%d');date=now.strftime('%Y%m%dT%H%M%SZ')
    payload=hashlib.sha256(body).hexdigest()
    names='host;x-amz-content-sha256;x-amz-date'
    headers=f'host:{host}\nx-amz-content-sha256:{payload}\nx-amz-date:{date}\n'
    uri=quote(path,safe='/')
    canonical='\n'.join([method,uri,'',headers,names,payload])
    scope=f'{day}/us-east-1/s3/aws4_request'
    string='AWS4-HMAC-SHA256\n'+date+'\n'+scope+'\n'+hashlib.sha256(canonical.encode()).hexdigest()
    key=('AWS4'+secret).encode()
    for part in [day,'us-east-1','s3','aws4_request']:key=hmac.new(key,part.encode(),hashlib.sha256).digest()
    signature=hmac.new(key,string.encode(),hashlib.sha256).hexdigest()
    auth=f'AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={names}, Signature={signature}'
    req=Request(endpoint.rstrip('/')+uri,data=body if method=='PUT' else None,method=method,
                headers={'Host':host,'X-Amz-Date':date,'X-Amz-Content-Sha256':payload,'Authorization':auth})
    with urlopen(req,timeout=30) as response:return response.read()


def main():
    p=argparse.ArgumentParser();p.add_argument('--credentials',required=True);p.add_argument('--url',default='http://127.0.0.1:9090');p.add_argument('--bucket',default='velero');p.add_argument('--output')
    args=p.parse_args();cfg=configparser.ConfigParser();cfg.read(args.credentials)
    access=cfg['default']['aws_access_key_id'];secret=cfg['default']['aws_secret_access_key']
    # Keep the probe within a valid Velero top-level directory. Some S3
    # gateways retain empty directory entries after an object is deleted.
    path='/'+args.bucket+'/backups/kr-store-check-'+uuid.uuid4().hex+'.txt';body=b'Kube Recover persistent S3 write verification'
    request('PUT',args.url,path,access,secret,body)
    try:
        if request('GET',args.url,path,access,secret)!=body:raise ValueError('S3 returned different bytes')
    finally:request('DELETE',args.url,path,access,secret)
    result={'accepted':True,'checks':['signed-put','exact-get','signed-delete'],'endpoint':args.url,'bucket':args.bucket}
    if args.output:Path(args.output).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))

if __name__=='__main__':main()
