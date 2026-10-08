"""Loopback-only preview using temporary router files and mocked services. No real /opt writes."""
import functools
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import test_runtime as fixture
import catalog
import devices
import backup
import explain
import performance
import slots
import vless_url
from test_ownership import Kernel
REAL_RUN=subprocess.run
from test_devices import DeviceRouter
from test_swap_slots import profile

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pivas_web',ROOT/'web-src/data/opt/etc/pivas-web/server.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)
rt=fixture.rt
router=fixture.RuntimeTests('test_changes_on_pause_are_activated_on_resume');router.setUp()

preview_config=json.loads(rt.path(rt.XRAY).read_text())
preview_config['outbounds']=[profile(1),profile(2)]
router.put(rt.XRAY,json.dumps(preview_config))

def create(name,slot,domains):
    catalog.handle(rt,dict(action='create',name=name,slot=slot,domains=domains,revision=catalog.revision(rt)))
create('YouTube',1,['youtube.com','youtu.be','googlevideo.com','ytimg.com','youtubei.googleapis.com','youtube-nocookie.com','yt3.ggpht.com','youtube.googleapis.com'])
create('Рабочие сервисы',2,['github.com','githubassets.com','githubusercontent.com'])
create('Музыка',1,['spotify.com','scdn.co','spotifycdn.com'])
g=catalog.view(rt)['groups'][2]
catalog.handle(rt,dict(action='toggle',id=g['id'],enabled=False,revision=catalog.revision(rt)))

device_router=DeviceRouter(Kernel(router.router))
router.device_patch=patch.object(rt,'command',device_router)
router.device_patch.start()

def run(args,**kwargs):
    try:
        if args[0]=='openssl':return REAL_RUN(args,**kwargs)
        if args==['pivas','vless','swap']:
            with rt.lock():value={'changed':rt.swap_slots()}
            return subprocess.CompletedProcess(args,0,json.dumps(value),'')
        if len(args)==4 and args[:2]==['pivas','vless'] and args[2] in ('set-1','set-2'):
            with rt.lock():
                config=json.loads(rt.path(rt.XRAY).read_text())
                candidate=vless_url.replace_slot(config,vless_url.parse(args[3]),int(args[2][-1]))
                rt.atomic(rt.path(rt.XRAY),json.dumps(candidate).encode())
            return subprocess.CompletedProcess(args,0,'','')
        if web.RUNTIME in args:
            with rt.lock():
                if args[2] in ('backup','explain'):
                    backend=backup if args[2]=='backup' else explain
                    value=backend.handle(rt,json.loads(kwargs['input']))
                elif args[2]=='performance':
                    label=json.loads(kwargs['input'])['label']
                    value=dict(available=True,label=label,cpu_percent=4 if label=='idle' else 32,memory_kib={'MemTotal':262144,'MemAvailable':173056},processes=[dict(pid=100,name='xray',rss_kib=24576,cpu_one_core_percent=1 if label=='idle' else 48)],dns=[dict(port=53,ms=8,ok=True),dict(port=9753,ms=10,ok=True),dict(port=9153,ms=45,ok=True),dict(port=9154,ms=58,ok=True)],note='Демонстрационные измерения, роутер не подключён.')
                else:
                    backend=slots if 'slots' in args else devices if 'devices' in args else catalog
                    value=backend.handle(rt,json.loads(kwargs['input']) if 'write' in args else None,**({'refresh':True} if 'refresh' in args else {}))
            return subprocess.CompletedProcess(args,0,json.dumps(value),'')
        if web.DIAGNOSTICS in args:
            return subprocess.CompletedProcess(args,0,b'Pivas diagnostic preview\nNo real router accessed.\n',b'')
        return subprocess.CompletedProcess(args,0,'','')
    except Exception as exc:
        return subprocess.CompletedProcess(args,1,'',str(exc))

class Handler(web.AuthCGIHandler):
    def _authorized(self):return True
    def do_GET(self):
        if self.path=='/cgi-bin/state.sh':
            main,second=rt.entries(catalog.MAIN),rt.entries(catalog.SECOND)
            profiles=vless_url.state(json.loads(rt.path(rt.XRAY).read_text()))
            return self._json(dict(xray=True,paused=False,vpn_enabled=True,route_ready=True,uptime='2 дн. 14 ч. · демо',mem_used=87,mem_total=256,slot1=dict(server=profiles['slot1']['server'],transport=profiles['slot1']['transport'],domains=sorted(main-second),url=''),slot2=dict(server=profiles['slot2']['server'],transport=profiles['slot2']['transport'],same_as_slot1=profiles['slot2_same'],domains=sorted(second),url='')))
        if self.path.startswith('/cgi-bin/ping.sh'):
            return self._json({'slot1':{'ok':True,'e2e_ms':82},'slot2':{'ok':True,'e2e_ms':64}})
        return super().do_GET()
    def log_message(self,*args):pass

if __name__=='__main__':
    with patch.object(web.subprocess,'run',run):
        server=ThreadingHTTPServer(('127.0.0.1',int(sys.argv[1]) if len(sys.argv)>1 else 8879),functools.partial(Handler,directory=str(ROOT/'web-src/data/opt/etc/pivas-web/www')))
        print('Preview at http://127.0.0.1:'+str(server.server_port),flush=True)
        try:server.serve_forever()
        finally:server.server_close();router.device_patch.stop();router.tearDown()
