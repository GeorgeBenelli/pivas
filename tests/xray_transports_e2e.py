#!/usr/bin/env python3
"""Official Xray loopback tests with real Pivas URI import and routing template.

Run with a native Xray 26.3.27 binary and native QUIC helper. Synthetic UUID,
keys, certificates and temporary configs only; never contacts a router.
Python linked to OpenSSL with TLS 1.3 support is required for the Reality decoy.
"""
import argparse
import contextlib
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'work/orig/data/opt/apps/pivas/bin/main'))
import vless_url as v
import runtime as rt
ID='00000000-0000-0000-0000-000000000001'
BODY=b'pivas-xray-transport-ok'

def port():
    while True:
        with socket.socket() as tcp,socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
            tcp.bind(('127.0.0.1',0));number=tcp.getsockname()[1]
            try:udp.bind(('127.0.0.1',number))
            except OSError:continue
            return number

class Target(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.send_header('Content-Length',str(len(BODY)))
        self.end_headers();self.wfile.write(BODY)
    def log_message(self,*args):pass

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--xray',required=True);parser.add_argument('--probe',required=True)
    args=parser.parse_args();assert 'Xray 26.3.27 ' in subprocess.check_output([args.xray,'version'],text=True)
    with tempfile.TemporaryDirectory(prefix='pivas-xray-transport-') as temp,contextlib.ExitStack() as cleanup:
        d=Path(temp);cert,key=d/'cert.pem',d/'key.pem';conf=d/'openssl.cnf'
        conf.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\n')
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-config',str(conf),'-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        http=ThreadingHTTPServer(('127.0.0.1',0),Target);threading.Thread(target=http.serve_forever,daemon=True).start();cleanup.callback(http.server_close);cleanup.callback(http.shutdown)
        tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key);tls.minimum_version=ssl.TLSVersion.TLSv1_3;tls.set_alpn_protocols(['h2'])
        decoy=ThreadingHTTPServer(('127.0.0.1',0),Target);decoy.socket=tls.wrap_socket(decoy.socket,server_side=True)
        threading.Thread(target=decoy.serve_forever,daemon=True).start();cleanup.callback(decoy.server_close);cleanup.callback(decoy.shutdown)
        keys=subprocess.check_output([args.xray,'x25519'],text=True).splitlines()
        private=next(l.split(':',1)[1].strip() for l in keys if l.startswith('PrivateKey'))
        public=next(l.split(':',1)[1].strip() for l in keys if l.startswith('Password'))
        real_run=subprocess.run
        def redirect_probe(argv,*a,**kw):
            if argv and argv[0]=='/opt/sbin/pivas-quic-probe':argv=[args.probe]+argv[1:]
            return real_run(argv,*a,**kw)
        def spawn(data,label,stack):
            config=d/(label+'.json');config.write_text(json.dumps(data));config.chmod(0o600)
            result=real_run([args.xray,'run','-test','-c',str(config)],capture_output=True,text=True,timeout=10)
            assert result.returncode==0,result.stdout+result.stderr
            log=(d/(label+'.log')).open('wb');stack.callback(log.close)
            proc=subprocess.Popen([args.xray,'run','-c',str(config)],stdout=log,stderr=subprocess.STDOUT)
            def stop():
                if proc.poll() is None:proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            stack.callback(stop);time.sleep(.3)
            assert proc.poll() is None,(d/(label+'.log')).read_text()
        cases=[('tcp','reality','',None),('grpc','tls','gun','h2')]
        cases += [('xhttp',security,mode,'h2' if security=='tls' else None)
                  for security in ('tls','reality') for mode in ('auto','packet-up','stream-up','stream-one')]
        cases += [('xhttp','tls','packet-up',alpn) for alpn in ('h3','http/1.1')]
        for number,(network,security,mode,alpn) in enumerate(cases):
            with contextlib.ExitStack() as stack:
                server_port=port();params=dict(type=network,security=security,sni='localhost',fp='edge')
                if security=='reality':params.update(pbk=public,sid='ab')
                else:params['alpn']=alpn
                if network=='xhttp':
                    extra={'headers':{'X-Pivas-Test':'fixture'}}
                    if security=='tls' and mode=='stream-up':
                        extra['downloadSettings']={'address':'127.0.0.1','port':server_port,'network':'xhttp','security':'tls',
                                                   'tlsSettings':{'serverName':'localhost','alpn':['h2'],'pinnedPeerCertSha256':pin},
                                                   'xhttpSettings':{'path':'/compat/','mode':'auto'}}
                    params.update(path='/compat/',host='localhost',mode=mode,extra=json.dumps(extra))
                elif network=='grpc':params.update(serviceName='fixture',mode='gun')
                link='vless://'+ID+'@127.0.0.1:'+str(server_port)+'?'+urlencode(params)
                parsed=v.parse(link);stream=copy.deepcopy(parsed['streamSettings'])
                if security=='reality':stream['realitySettings']={'dest':'127.0.0.1:'+str(decoy.server_port),'serverNames':['localhost'],'privateKey':private,'shortIds':['ab']}
                else:stream['tlsSettings']={'alpn':[alpn],'certificates':[{'certificateFile':str(cert),'keyFile':str(key)}]}
                if network=='xhttp':
                    stream['xhttpSettings']['mode']='auto'
                    stream['xhttpSettings'].get('extra',{}).pop('downloadSettings',None)
                server={'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':server_port,'protocol':'vless','settings':{'decryption':'none','clients':[{'id':ID,'flow':'xtls-rprx-vision' if network=='tcp' else ''}]},'streamSettings':stream}],'outbounds':[{'protocol':'freedom'}]}
                spawn(server,str(number)+'-server',stack)
                with patch.object(subprocess,'run',side_effect=redirect_probe):prepared=v.prepare(link)
                if security=='tls':assert prepared['streamSettings']['tlsSettings']['pinnedPeerCertSha256']==pin
                assert v.parse(v.serialize(prepared))==prepared
                socks=port();client={'log':{'loglevel':'warning'},'inbounds':[{'listen':'127.0.0.1','port':socks,'protocol':'socks','settings':{'auth':'noauth','udp':True}}],'outbounds':[dict(prepared,tag='slot-1',mux={'enabled':False})]}
                spawn(client,str(number)+'-client',stack)
                reply=real_run(['curl','-fsS','--noproxy','','--max-time','8','--socks5-hostname','127.0.0.1:'+str(socks),'http://127.0.0.1:'+str(http.server_port)],capture_output=True,timeout=12)
                assert reply.returncode==0 and reply.stdout==BODY,(d/(str(number)+'-client.log')).read_text()+reply.stderr.decode()
                # The complete shipped routing/DNS template must also accept the
                # same imported profile in either slot, with firmware marks.
                base=json.loads((ROOT/'work/orig/data/opt/apps/pivas/etc/conf/pivas.vless').read_text())
                base['inbounds'][0]['port']=1097;base['log']={'loglevel':'warning'}
                for slot in (1,2):base=v.replace_slot(base,prepared,slot)
                for out in base['outbounds']:
                    if out.get('tag') in ('slot-1','slot-2'):out['streamSettings']['sockopt']={'mark':851969,'tcpFastOpen':True}
                base=rt.routing(base,{'example.com','sub.example.com','192.0.2.0/24'},{'sub.example.com','192.0.2.0/24'},['removed.example'])
                config=d/'template.json';config.write_text(json.dumps(base))
                result=real_run([args.xray,'run','-test','-c',str(config)],capture_output=True,text=True,timeout=10)
                assert result.returncode==0,result.stdout+result.stderr
                print(network,security,mode or 'vision',alpn or '',': import, certificate, export, HTTP and two-slot template PASS',flush=True)
        print('Official Xray 26.3.27: 12 transport scenarios PASS',flush=True)

if __name__=='__main__':main()
