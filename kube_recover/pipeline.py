"""Guided experiments: explicit simulation, real local recovery, or real S3 round trip."""
import json,time,uuid
from urllib.request import urlopen
from threading import Thread
from .console import Workload
from . import schema

class Pipeline:
    def __init__(self,server):self.server=server
    def run(self,params):
        mode=params.get('mode','simulation');scenario=params.get('scenario','success')
        if mode not in {'simulation','local','storage'}:raise ValueError('Choose simulation, local execution or storage execution')
        if scenario not in {'success','data_fault','unsupported','storage_outage'}:raise ValueError('Unknown pipeline scenario')
        if mode=='local' and scenario=='storage_outage':raise ValueError('Choose simulation or storage mode to demonstrate a storage outage')
        names=[('source','Inspect protected application'),('backup','Create consistent snapshot'),('upload','Transfer to object storage'),('download','Retrieve protected backup'),('compatibility','Check application compatibility'),('restore','Stage restored application'),('conversion','Convert version 1 to version 2'),('verification','Verify logical state'),('service','Probe recovered HTTP service'),('complete','Retain run evidence')]
        state={'id':uuid.uuid4().hex[:12],'mode':mode,'scenario':scenario,'simulated':mode=='simulation','status':'running','started_at':time.time(),'steps':[{'id':k,'label':v,'status':'pending'} for k,v in names]}
        self.server.pipeline=state
        def stage(key,message,fn):
            step=next(x for x in state['steps'] if x['id']==key);step.update(status='running',started_at=time.time());self.server.event(key,message)
            try:result=fn()
            except Exception as e:
                step.update(status='failed',error=str(e),elapsed_ms=round((time.time()-step['started_at'])*1000,2));raise
            step.update(status='simulated' if state['simulated'] else 'completed',elapsed_ms=round((time.time()-step['started_at'])*1000,2));return result
        try:
            if mode=='simulation':
                for key,label in names:
                    def illustrate(k=key):
                        time.sleep(.4)
                        fail={'unsupported':'compatibility','storage_outage':'upload','data_fault':'verification'}.get(scenario)
                        if k==fail:raise ValueError({'unsupported':'Simulated rejection: no approved converter for version 3.0','storage_outage':'Simulated object-storage outage','data_fault':'Simulated logical-state mismatch detected'}[scenario])
                    stage(key,'Simulation: '+label,illustrate)
                result={'simulated':True,'accepted':False,'outcome':'Simulation completed; no backup, storage transfer or cluster operation was executed'}
            else:
                r=self.server.recovery
                stage('source','Inspecting the real source database',lambda:schema.summary(r.source))
                backup=stage('backup','Creating a real transaction-consistent snapshot',lambda:r.backup(self.server.event));key=backup['backup_id']
                if mode=='storage':
                    if scenario=='storage_outage':
                        def outage():raise ValueError('Selected controlled storage-outage scenario; transfer intentionally blocked')
                        stage('upload','Demonstrating a blocked transfer',outage)
                    upload=stage('upload','Uploading and reading back real S3 objects',lambda:self.server.storage.upload(r,key))
                    download=stage('download','Downloading and verifying the stored backup',lambda:self.server.storage.fetch_backup(r,key));key=download['backup_id']
                else:
                    for k in ['upload','download']:
                        next(x for x in state['steps'] if x['id']==k).update(status='skipped',reason='Local mode uses a local backup; no S3 transfer')
                target='3.0' if scenario=='unsupported' else '2.0'
                stage('compatibility','Checking the declared converter policy',lambda:schema.compatibility('1.0',target))
                # The recovery engine emits its actual internal events; do not invent stage durations.
                def capture(phase,message):
                    self.server.event(phase,message)
                    mapped={'restore':'restore','conversion':'conversion','verification':'verification','publish':'verification'}.get(phase)
                    if mapped:
                        now=time.time()
                        for step in state['steps']:
                            if step['status']=='running' and step['id']!=mapped:
                                step.update(status='completed',elapsed_ms=round((now-step['started_at'])*1000,2))
                        step=next(x for x in state['steps'] if x['id']==mapped)
                        if step['status']=='pending':step.update(status='running',started_at=now)
                recovery=r.recover(key,target,capture)
                for step in state['steps']:
                    if step['status']=='running':step.update(status='completed',elapsed_ms=round((time.time()-step['started_at'])*1000,2))
                if scenario=='data_fault':
                    self.server.event('fault','Injecting and detecting a controlled target order-total fault')
                    r.corrupt_target()
                    if r.status()['target']['ready']:raise ValueError('Controlled fault was not detected')
                    recovery=r.recover(key,target,self.server.event)
                    state['fault_detected']=True;state['fault_repaired']=True
                def probe():
                    app=Workload(('127.0.0.1',0),r.target,target);worker=Thread(target=app.serve_forever,daemon=True);worker.start()
                    try:
                        for _ in range(12):
                            with urlopen(f'http://127.0.0.1:{app.server_port}/orders',timeout=20) as response:
                                if response.status!=200 or json.load(response)['application_version']!=target:raise ValueError('Recovered service check failed')
                    finally:app.shutdown();app.server_close();worker.join()
                    return {'requests':12,'errors':0,'transport':'loopback HTTP'}
                service=stage('service','Executing 12 real post-recovery HTTP probes',probe)
                result={'simulated':False,'accepted':recovery['accepted'],'recovery':recovery,'service':service,'mode':mode}
                if mode=='storage':result.update(storage_upload=upload,storage_download=download)
                stage('complete','Saving the pipeline acceptance evidence',lambda:None)
            state.update(status='completed',result=result,finished_at=time.time())
            return result
        except Exception as e:
            for step in state['steps']:
                if step['status']=='running':step.update(status='failed',error=str(e))
            state.update(status='failed',error=str(e),finished_at=time.time())
            raise
        finally:
            folder=self.server.recovery.root/'pipelines';folder.mkdir(exist_ok=True)
            (folder/(state['id']+'.json')).write_text(json.dumps(state,indent=2),encoding='utf-8')
