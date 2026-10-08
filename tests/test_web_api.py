import importlib.util
import io
import json
from email.message import Message
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('web_api_test',ROOT/'web-src/data/opt/etc/pivas-web/server.py')
web=importlib.util.module_from_spec(spec);spec.loader.exec_module(web)

class Request(web.AuthCGIHandler):
    def __init__(self,route,payload=None,origin='http://192.168.1.1:8888',cookie=None):
        self.path=route;self.command='GET' if payload is None else 'POST';self.headers=Message()
        self.headers['Host']='192.168.1.1:8888';self.headers['Origin']=origin
        self.headers['Content-Type']='application/json'
        if cookie:self.headers['Cookie']=cookie
        body=json.dumps(payload).encode() if payload is not None else b''
        self.headers['Content-Length']=str(len(body));self.rfile=io.BytesIO(body);self.wfile=io.BytesIO();self.status=None;self.response_headers={}
    def send_response(self,status):self.status=status
    def send_header(self,k,v):self.response_headers[k]=v
    def end_headers(self):pass
    def log_message(self,*args):pass
    def data(self):return json.loads(self.wfile.getvalue())

class WebApiTests(unittest.TestCase):
    def test_swap_is_one_command_and_returns_no_links(self):
        request=Request('/api/control',{'action':'swap'})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"changed":true}','')) as run:request._api()
        self.assertEqual(run.call_args.args[0],['pivas','vless','swap'])
        self.assertEqual(request.data(),{'ok':True,'changed':True})
        self.assertNotIn('shell',run.call_args.kwargs)
    def test_swap_rejects_unauthenticated_and_foreign_origin(self):
        for authenticated in (False, True):
            request=Request('/api/control',{'action':'swap'},origin='http://evil.example')
            with patch.object(web.subprocess,'run') as run:
                request._api() if authenticated else request.do_POST()
            self.assertEqual(request.status,403 if authenticated else 401);run.assert_not_called()
    def test_new_tools_require_auth_and_same_origin(self):
        for route in ('/api/backup','/api/explain','/api/performance','/api/slots'):
            for authenticated in (False,True):
                request=Request(route,{},origin='http://evil.example')
                with patch.object(web.subprocess,'run') as run:
                    request._api() if authenticated else request.do_POST()
                self.assertEqual(request.status,403 if authenticated else 401);run.assert_not_called()
    def test_backup_password_goes_to_stdin_not_argv_and_errors_are_redacted(self):
        request=Request('/api/backup',{'action':'preview','password':'FAKE_SECRET'})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'FAKE_SECRET','FAKE_SECRET')) as run:request._api()
        self.assertNotIn('FAKE_SECRET',str(run.call_args.args));self.assertIn('FAKE_SECRET',run.call_args.kwargs['input'])
        self.assertNotIn('FAKE_SECRET',request.wfile.getvalue().decode())
    def test_explicit_device_refresh_reaches_runtime(self):
        request=Request('/api/devices?refresh=1')
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"devices":[]}','')) as run:request._api()
        self.assertEqual(run.call_args.args[0][-2:],['devices','refresh'])
    def test_devices_use_same_auth_and_origin_guards(self):
        for authenticated in (False, True):
            request=Request('/api/devices',{},origin='http://evil.example')
            with patch.object(web.subprocess,'run') as run:
                request._api() if authenticated else request.do_POST()
            self.assertEqual(request.status,403 if authenticated else 401)
            run.assert_not_called()
    def test_devices_json_write_and_current_device_hint(self):
        payload={'revision':'rev','mac':'02:11:22:33:44:55','excluded':True}
        request=Request('/api/devices',payload);request.client_address=('192.168.1.10',1234)
        result={'devices':[{'ip':'192.168.1.10'},{'ip':'192.168.1.20'}]}
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,json.dumps(result),'')) as run:request._api()
        args,kwargs=run.call_args
        self.assertEqual(args[0],[web.sys.executable,web.RUNTIME,'devices','write'])
        self.assertEqual(json.loads(kwargs['input']),payload);self.assertNotIn('shell',kwargs)
        self.assertEqual([d['is_current'] for d in request.data()['devices']],[True,False])
    def test_cross_origin_rejected_before_command(self):
        request=Request('/api/catalog',{},origin='http://evil.example')
        with patch.object(web.subprocess,'run') as run:request._api()
        self.assertEqual(request.status,403);run.assert_not_called()
    def test_unauthenticated_write_rejected(self):
        request=Request('/api/catalog',{})
        with patch.object(web.subprocess,'run') as run:request.do_POST()
        self.assertEqual(request.status,401);run.assert_not_called()
    def test_catalog_uses_json_stdin_without_shell(self):
        payload={'action':'create','name':'$(touch /tmp/no)','domains':['example.org']}
        request=Request('/api/catalog',payload)
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"groups":[]}','')) as run:request._api()
        args,kwargs=run.call_args
        self.assertEqual(args[0],[web.sys.executable,web.RUNTIME,'catalog','write'])
        self.assertEqual(json.loads(kwargs['input']),payload);self.assertNotIn('shell',kwargs)
        self.assertEqual(request.status,200)
    def test_slot_name_reaches_shared_runtime_as_json_without_shell(self):
        payload={'action':'rename','slot':2,'name':'$(touch /tmp/no)','revision':'revision'}
        request=Request('/api/slots',payload)
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'{"names":{"1":"One","2":"Two"}}','')) as run:request._api()
        self.assertEqual(run.call_args.args[0],[web.sys.executable,web.RUNTIME,'slots','write'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']),payload)
        self.assertNotIn('shell',run.call_args.kwargs)
        self.assertEqual(request.status,200)
    def test_backend_failure_is_http_error_not_false_success(self):
        request=Request('/api/catalog',{})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'','Домен занят группой')):request._api()
        self.assertEqual(request.status,409);self.assertEqual(request.data()['error'],'Домен занят группой')
    def test_diagnostic_download_requires_post_and_has_attachment(self):
        get=Request('/api/diagnostics');get._api();self.assertEqual(get.status,405)
        request=Request('/api/diagnostics',{})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,b'safe report',b'')):request._api()
        self.assertEqual(request.wfile.getvalue(),b'safe report');self.assertIn('attachment',request.response_headers['Content-Disposition'])
    def test_invalid_slot_or_payload_does_not_execute(self):
        for payload in [[],{'action':'set','slot':True,'url':'vless://x'},{'action':'rm','slot':1}]:
            request=Request('/api/control',payload)
            with patch.object(web.subprocess,'run') as run:request._api()
            self.assertEqual(request.status,400);run.assert_not_called()
    def test_control_error_does_not_echo_credentials(self):
        request=Request('/api/control',{'action':'set','slot':1,'url':'vless://FAKE_SECRET'})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],1,'FAKE_SECRET','FAKE_SECRET')):request._api()
        self.assertEqual(request.status,409);self.assertNotIn(b'FAKE_SECRET',request.wfile.getvalue())
    def test_bot_status_reads_config_without_exposing_or_executing_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            config=Path(tmp)/'bot.py'
            config.write_text('token = "123456789:AAFAKE_SECRET_12345678901234567890"\nuserid = [123, -456]\nraise RuntimeError("must not execute")\n')
            with patch.object(web,'BOT_CONFIG',str(config)),patch.object(web,'BOT_PID',str(Path(tmp)/'missing.pid')):
                status=web.bot_status()
        self.assertEqual(status,{'token_set':True,'ids':['123','-456'],'running':False})
        self.assertNotIn('FAKE_SECRET',json.dumps(status))
    def test_bot_api_requires_session_and_same_origin(self):
        request=Request('/api/bot',{'action':'start'})
        with patch.object(web.subprocess,'run') as run:request.do_POST()
        self.assertEqual(request.status,401);run.assert_not_called()
        request=Request('/api/bot',{'action':'start'},origin='http://evil.example')
        with patch.object(web.subprocess,'run') as run:request._api()
        self.assertEqual(request.status,403);run.assert_not_called()
    def test_bot_token_and_ids_use_cli_without_echoing_secret(self):
        token='123456789:AAFAKE_SECRET_12345678901234567890'
        status={'token_set':True,'ids':['123','-456'],'running':False}
        with patch.object(web,'bot_status',return_value=status),patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
            request=Request('/api/bot',{'action':'token','token':token});request._api()
            self.assertEqual(run.call_args.args[0],['pivas','bot','token',token])
            self.assertNotIn(token,request.wfile.getvalue().decode())
            request=Request('/api/bot',{'action':'ids','ids':'123\n-456, 123'});request._api()
            self.assertEqual(run.call_args.args[0],['pivas','bot','id','123','-456'])
        self.assertEqual(request.data(),status)
    def test_bot_invalid_input_and_cli_error_do_not_leak(self):
        for payload in ({'action':'token','token':'123\nprint(1)'},{'action':'ids','ids':'123\n__import__("os")'},{'action':'ids','ids':''}):
            request=Request('/api/bot',payload)
            with patch.object(web.subprocess,'run') as run:request._api()
            self.assertEqual(request.status,400);run.assert_not_called()
        token='123456789:AAFAKE_SECRET_12345678901234567890'
        request=Request('/api/bot',{'action':'token','token':token})
        with patch.object(web.subprocess,'run',return_value=subprocess.CompletedProcess([],1,token,token)):request._api()
        self.assertEqual(request.status,409);self.assertNotIn(token,request.wfile.getvalue().decode())

if __name__=='__main__':unittest.main()
