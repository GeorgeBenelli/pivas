#!/usr/bin/env python3
"""Loopback interoperability with official Xray and Hysteria binaries.

Run explicitly: python3 tests/hysteria_e2e.py --xray PATH --hysteria PATH
--probe PATH. No external endpoint, router configuration or credentials used.
"""
import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import socket
import select
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from unittest.mock import patch
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('connections', ROOT/'work/orig/data/opt/apps/pivas/bin/main/vless_url.py')
v = importlib.util.module_from_spec(spec); spec.loader.exec_module(v)

class HTTP(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(b'pivas-hysteria-tcp-ok')
    def log_message(self,*args):pass

def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET,kind) as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

def read_exact(sock,length):
    out=b''
    while len(out)<length:
        data=sock.recv(length-len(out))
        if not data:raise RuntimeError('SOCKS connection closed')
        out+=data
    return out

def udp_echo(sock,stop):
    sock.settimeout(.2)
    while not stop.is_set():
        try:
            data,addr=sock.recvfrom(4096);sock.sendto(data,addr)
        except socket.timeout:pass


def hopping_relay(server_port, cleanup):
    """Two ingress ports share one backend flow; no separate QUIC sessions."""
    entries=[]
    for _ in range(2):
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.bind(('127.0.0.1',0))
        entries.append(sock);cleanup.callback(sock.close)
    backend=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);backend.connect(('127.0.0.1',server_port));cleanup.callback(backend.close)
    stop=threading.Event();counts=[0,0];state={'client':None,'entry':entries[0]}
    def relay():
        while not stop.is_set():
            for sock in select.select(entries+[backend],[],[],.1)[0]:
                if sock is backend:
                    data=sock.recv(65535)
                    if state['client']:state['entry'].sendto(data,state['client'])
                else:
                    data,addr=sock.recvfrom(65535);counts[entries.index(sock)]+=1
                    state.update(client=addr,entry=sock);backend.send(data)
    thread=threading.Thread(target=relay,daemon=True);thread.start()
    def close():stop.set();thread.join(timeout=1)
    cleanup.callback(close)
    return [s.getsockname()[1] for s in entries], counts

def socks_udp(socks_port,target_port):
    with socket.create_connection(('127.0.0.1',socks_port),timeout=4) as tcp, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as udp:
        tcp.sendall(b'\x05\x01\x00');assert read_exact(tcp,2)==b'\x05\x00'
        udp.bind(('127.0.0.1',0));udp.settimeout(4)
        tcp.sendall(b'\x05\x03\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',udp.getsockname()[1]))
        head=read_exact(tcp,4);assert head[:2]==b'\x05\x00' and head[3]==1
        host=socket.inet_ntoa(read_exact(tcp,4));relay_port=struct.unpack('!H',read_exact(tcp,2))[0]
        if host=='0.0.0.0':host='127.0.0.1'
        payload=b'pivas-hysteria-udp-ok'
        udp.sendto(b'\x00\x00\x00\x01'+socket.inet_aton('127.0.0.1')+struct.pack('!H',target_port)+payload,(host,relay_port))
        answer,_=udp.recvfrom(4096)
        assert answer.endswith(payload), 'Hysteria UDP response differs'

def main():
    parser=argparse.ArgumentParser()
    for name in ('xray','hysteria','probe'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='pivas-hysteria-e2e-') as tmp, contextlib.ExitStack() as cleanup:
        directory=Path(tmp)
        conf=directory/'openssl.cnf'
        conf.write_text('[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=localhost\n[ext]\nsubjectAltName=DNS:localhost\n')
        cert,key=directory/'cert.pem',directory/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1','-config',str(conf),'-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
        expected_pin=hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
        http=ThreadingHTTPServer(('127.0.0.1',0),HTTP)
        threading.Thread(target=http.serve_forever,daemon=True).start();cleanup.callback(http.server_close);cleanup.callback(http.shutdown)
        udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);udp.bind(('127.0.0.1',0))
        stop=threading.Event();threading.Thread(target=udp_echo,args=(udp,stop),daemon=True).start()
        cleanup.callback(udp.close);cleanup.callback(stop.set)
        processes=[]
        def spawn(argv,label):
            log=(directory/(label+'.log')).open('wb');cleanup.callback(log.close)
            proc=subprocess.Popen(argv,stdout=log,stderr=subprocess.STDOUT);processes.append(proc)
            def close():
                if proc.poll() is None:proc.terminate()
                try:proc.wait(timeout=3)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
            cleanup.callback(close)
            return proc
        original_run=subprocess.run
        def redirect_probe(argv,*a,**kw):
            if argv and argv[0]=='/opt/sbin/pivas-quic-probe':argv=[args.probe]+argv[1:]
            return original_run(argv,*a,**kw)
        for mode in ('plain','salamander','hopping'):
            with contextlib.ExitStack() as mode_cleanup:
                server_port=port(socket.SOCK_DGRAM)
                server=dict(listen='127.0.0.1:'+str(server_port),tls=dict(cert=str(cert),key=str(key)),auth=dict(type='password',password='fixture:pass/word'))
                query=dict(sni='localhost',insecure='1')
                if mode=='salamander':
                    server['obfs']=dict(type='salamander',salamander=dict(password='fixture-mask'))
                    query.update(obfs='salamander',**{'obfs-password':'fixture-mask'})
                config=directory/(mode+'-server.json');config.write_text(json.dumps(server))
                server_process=spawn([args.hysteria,'server','-c',str(config)],mode+'-server')
                mode_cleanup.callback(server_process.terminate)
                if mode=='hopping':
                    ingress,counts=hopping_relay(server_port,mode_cleanup)
                    server_port,second_port=ingress
                    # A port different from the URI endpoint must receive the
                    # actual authenticated traffic. A config-only check misses
                    # fields silently ignored by newer Xray versions.
                    query.update(mport=str(second_port),hopInterval='5',upmbps='50',downmbps='100')
                time.sleep(.3)
                assert server_process.poll() is None, (directory/(mode+'-server.log')).read_text()
                link='hy2://fixture%3Apass%2Fword@127.0.0.1:'+str(server_port)+'/?'+urlencode(query)
                with patch.object(subprocess,'run',side_effect=redirect_probe):out=v.prepare(link)
                if mode=='hopping':counts[:]=[0,0]
                assert out['streamSettings']['tlsSettings']['pinnedPeerCertSha256']==expected_pin
                assert 'allowInsecure' not in out['streamSettings']['tlsSettings']
                socks=port()
                xray_config=dict(log=dict(loglevel='warning'),inbounds=[dict(listen='127.0.0.1',port=socks,protocol='socks',settings=dict(auth='noauth',udp=True))],outbounds=[dict(out,tag='slot-1',mux=dict(enabled=False))])
                config=directory/(mode+'-client.json');config.write_text(json.dumps(xray_config))
                validate=original_run([args.xray,'run','-test','-c',str(config)],capture_output=True,text=True)
                assert validate.returncode==0,validate.stdout+validate.stderr
                client=spawn([args.xray,'run','-c',str(config)],mode+'-client');mode_cleanup.callback(client.terminate)
                time.sleep(.25)
                def http_check():
                    reply=original_run(['curl','-fsS','--noproxy','','--max-time','5','--socks5-hostname','127.0.0.1:'+str(socks),'http://127.0.0.1:'+str(http.server_port)],capture_output=True)
                    assert reply.returncode==0 and reply.stdout==b'pivas-hysteria-tcp-ok', (directory/(mode+'-client.log')).read_text()+reply.stderr.decode()
                http_check();socks_udp(socks,udp.getsockname()[1])
                if mode=='hopping':
                    time.sleep(5.2);http_check();socks_udp(socks,udp.getsockname()[1])
                    assert counts[1]>0 and counts[0]==0, 'Xray ignored the configured mport'
                print(mode+': native config, QUIC pin, TCP and UDP passed',flush=True)
        print('Official Xray / Hysteria 2 loopback interoperability passed.',flush=True)

if __name__=='__main__':main()
