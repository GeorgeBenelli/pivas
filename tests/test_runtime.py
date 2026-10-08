import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'work/orig/data/opt/apps/pivas/bin/main/runtime.py'
spec = importlib.util.spec_from_file_location('pivas_runtime', MODULE)
rt = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(MODULE.parent))
sys.modules[spec.name] = rt
spec.loader.exec_module(rt)


class Router:
    def __init__(self):
        self.services = {rt.SX: True, rt.S1: True, rt.S2: False, rt.SD: True}
        self.ipset_names = {'PIVAS_LIST','PIVAS_LEGACY'}
        self.calls = []
        self.fail = None
    def __call__(self, *args, **kwargs):
        self.calls.append(args)
        if self.fail and self.fail(args):
            self.fail = None
            raise RuntimeError('simulated failure: ' + ' '.join(args))
        out = ''
        if args[0] in self.services:
            if args[1] == 'status': out = 'alive' if self.services[args[0]] else 'dead'
            else: self.services[args[0]] = args[1] != 'stop'
        elif args[0] == 'pidof': out = '1234'
        elif args[0] == 'curl': out = '[{"status":"success"}]'
        elif args[0] == 'dig': out = '192.0.2.5\n'
        elif args[0] == '/opt/sbin/ipset':
            if args[1:]==('list','-name'):out='\n'.join(sorted(self.ipset_names))
            elif args[1]=='restore':
                for line in kwargs.get('input_text','').splitlines():
                    parts=line.split()
                    if parts and parts[0]=='create':self.ipset_names.add(parts[1])
                    elif parts and parts[0]=='destroy':self.ipset_names.discard(parts[1])
        return subprocess.CompletedProcess(args, 0, out, '')
    def restarts(self):
        return [c for c in self.calls if len(c) > 1 and c[1] in ('start', 'stop', 'restart')]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='pivas-tests-')
        self.base = Path(self.tmp.name)
        self.oldbase = rt.BASE
        rt.BASE = self.base
        self.router = Router()
        self.mock = patch.object(rt, 'command', self.router)
        self.mock.start()
        self.put('/opt/etc/pivas.conf', 'DNS_CRYPT_PORT=9153\nDNSMASQ_PORT=9753\nDNS_PROVIDER=\nDNS_VPN_FAILURE=provider\n')
        self.put('/opt/etc/pivas.list', 'example.com\nvideo.example.com\nordinary.test\n')
        self.put('/opt/etc/pivas-slot2.list', 'example.com\n')
        self.put(rt.XRAY, json.dumps({'inbounds':[], 'outbounds':[{'tag':'slot-1'},{'tag':'slot-2'}], 'routing':{'rules':[]}}))
        self.put(rt.DNSMASQ, 'port=9753\nno-resolv\nserver=127.0.0.1#9153\n')
        self.put(rt.TOML1, "listen_addresses = ['127.0.0.1:9153']\n")
        rt.path('/opt/tmp').mkdir(parents=True)
        for service in self.router.services: self.put(service, '#!/bin/sh\n')
    def tearDown(self):
        self.mock.stop()
        rt.BASE = self.oldbase
        self.tmp.cleanup()
    def put(self, name, text):
        p = rt.path(name); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    def test_provider_has_no_silent_public_fallback(self):
        self.assertEqual(rt.upstreams({}), ['127.0.0.1#53'])
        self.assertEqual(rt.upstreams({'DNS_PROVIDER':'192.0.2.1;192.0.2.2#5353'}), ['192.0.2.1#53','192.0.2.2#5353'])
    def test_provider_rejects_loops_and_config_injection(self):
        for value in ('127.0.0.1#9753','127.0.0.1#9154','8.8.8.8\nserver=/test/1.1.1.1','1.1.1.1#65536'):
            with self.subTest(value=value), self.assertRaises(ValueError): rt.upstreams({'DNS_PROVIDER':value})
    def test_child_domain_and_network_override_parent_slot(self):
        cfg = json.loads(rt.path(rt.XRAY).read_text())
        result = rt.routing(cfg, {'example.com','video.example.com','192.0.2.0/24','192.0.2.1/32'}, {'example.com','192.0.2.0/24'})
        rules = result['routing']['rules']
        dr = [r for r in rules if 'domain' in r]
        self.assertEqual([(r['domain'][0],r['outboundTag']) for r in dr], [('domain:video.example.com','slot-1'),('domain:example.com','slot-2')])
        self.assertEqual([r['outboundTag'] for r in rules if 'ip' in r], ['slot-1','slot-2'])
    def test_dns_child_uses_same_slot_as_xray(self):
        output = rt.servers({}, {'example.com','video.example.com'}, {'example.com'})
        self.assertIn('server=/video.example.com/127.0.0.1#9153',output)
        self.assertIn('server=/example.com/127.0.0.1#9154',output)
    def test_provider_fallback_does_not_inherit_parent_slot(self):
        output = rt.servers({'DNS_VPN_FAILURE':'provider'}, {'example.com','video.example.com'}, {'example.com'}, down1=True)
        self.assertIn('server=/video.example.com/127.0.0.1#53',output)
    def test_default_failure_is_closed_and_default_resolver_uses_doh_443(self):
        output = rt.servers({}, {'example.com'}, set(), down1=True)
        self.assertIn('server=127.0.0.1#53\n',output)
        self.assertIn('server=/example.com/\n',output)
        self.assertNotIn('server=/example.com/127.0.0.1#53',output)
        self.assertIn("stamp = '%s'" % rt.STAMP,rt.dnscrypt({}, 1))
        self.assertIn('proxy = \'socks5://127.0.0.1:1096\'',rt.dnscrypt({}, 1))
    def test_closed_and_slot2_fallback_are_explicit(self):
        output = rt.servers({'DNS_VPN_FAILURE':'closed'}, {'example.com'}, {'example.com'}, down2=True)
        self.assertIn('server=/example.com/\n',output)
        output = rt.servers({'DNS_SLOT2_FALLBACK':'slot1'}, {'example.com'}, {'example.com'}, down2=True)
        self.assertIn('server=/example.com/127.0.0.1#9153',output)
    def test_slot2_proxy_is_correct_on_first_creation(self):
        rt.apply()
        text = rt.path(rt.TOML2).read_text()
        self.assertIn("proxy = 'socks5://127.0.0.1:1099'",text)
        self.assertIn('force_tcp = true',text)
        self.assertNotIn('1096',text)
        self.assertNotIn('ip-api',text)
        self.assertTrue(self.router.services[rt.S2])
    def test_idempotent_apply_does_not_restart_services(self):
        self.assertTrue(rt.apply())
        self.router.calls.clear()
        self.assertFalse(rt.apply())
        self.assertEqual(self.router.restarts(),[])
    def test_dead_service_is_started_even_when_config_is_unchanged(self):
        rt.apply(); self.router.calls.clear(); self.router.services[rt.S2] = False
        self.assertFalse(rt.apply())
        self.assertEqual(self.router.restarts(), [(rt.S2,'start')])
    def test_move_existing_domain_updates_dns_immediately(self):
        rt.apply(); self.router.calls.clear()
        self.put('/opt/etc/pivas-slot2.list','')
        rt.apply()
        self.assertIn('server=/example.com/127.0.0.1#9153',rt.path(rt.SERVERS).read_text())
        self.assertFalse(self.router.services[rt.S2])
        self.assertIn(('kill','-HUP','1234'),self.router.calls)
    def test_add_main_domain_does_not_restart_xray_or_dnscrypt(self):
        rt.apply(); self.router.calls.clear()
        with rt.path('/opt/etc/pivas.list').open('a') as f:f.write('new.test\n')
        rt.apply()
        self.assertEqual(self.router.restarts(),[(rt.SD,'stop'),(rt.SD,'start')])
        self.assertIn('server=/new.test/127.0.0.1#9153',rt.path(rt.SERVERS).read_text())
    def test_validation_failure_leaves_all_files_untouched(self):
        before = {n:rt.path(n).read_bytes() for n in (rt.XRAY,rt.DNSMASQ,rt.TOML1)}
        self.router.fail = lambda args: args[0] == '/opt/sbin/dnscrypt-proxy'
        with self.assertRaises(RuntimeError):rt.apply()
        self.assertEqual(before,{n:rt.path(n).read_bytes() for n in before})
        self.assertEqual(self.router.restarts(),[])
        self.assertFalse(rt.path(rt.TOML2).exists())
    def test_service_failure_rolls_back_files_and_started_services(self):
        before = {n:rt.path(n).read_bytes() for n in (rt.XRAY,rt.DNSMASQ,rt.TOML1)}
        self.router.fail = lambda args: args == (rt.SD,'start')
        with self.assertRaises(RuntimeError):rt.apply()
        self.assertEqual(before,{n:rt.path(n).read_bytes() for n in before})
        self.assertFalse(rt.path(rt.TOML2).exists())
        self.assertFalse(self.router.services[rt.S2])
        self.assertTrue(self.router.services[rt.SX])
    def test_pause_does_not_start_dns_services(self):
        self.put('/opt/etc/pivas.paused','')
        self.router.services = {s:False for s in self.router.services}
        rt.apply()
        self.assertEqual(self.router.restarts(),[])
    def test_changes_on_pause_are_activated_on_resume(self):
        rt.apply(); self.router.calls.clear()
        self.put('/opt/etc/pivas.paused','')
        self.put('/opt/etc/pivas.conf',rt.path('/opt/etc/pivas.conf').read_text().replace('DNS_PROVIDER=','DNS_PROVIDER=192.0.2.53'))
        rt.apply()
        self.assertTrue(rt.path(rt.PENDING).exists())
        self.assertEqual(self.router.restarts(),[])
        rt.path('/opt/etc/pivas.paused').unlink()
        rt.apply()
        self.assertIn(('kill','-HUP','1234'),self.router.calls)
        self.assertFalse(rt.path(rt.PENDING).exists())
    def test_paused_first_apply_restarts_services_when_resumed(self):
        self.put('/opt/etc/pivas.paused','')
        rt.apply(); self.router.calls.clear()
        rt.path('/opt/etc/pivas.paused').unlink()
        rt.apply()
        self.assertIn((rt.S1,'restart'),self.router.restarts())
        self.assertIn((rt.SD,'start'),self.router.restarts())
    def test_servers_file_readable_after_dnsmasq_drops_privileges(self):
        rt.apply()
        self.assertEqual(rt.path(rt.SERVERS).stat().st_mode & 0o777,0o644)
    def test_existing_foreign_proxy_is_not_overwritten(self):
        self.put('/opt/etc/pivas.paused','')
        for f in ('xray','dnsmasq','dnscrypt-proxy'):self.put('/opt/sbin/'+f,'binary')
        def rci(*args,**kwargs):
            self.router.calls.append(args)
            return subprocess.CompletedProcess(args,0,'{"Proxy21":{"description":"Another VPN"}}','')
        with patch.object(rt,'command',rci),self.assertRaises(RuntimeError):rt.prepare()
        self.assertEqual(len(self.router.calls),1)
    def test_detach_removes_managed_guest_jumps_and_legacy_telegram_rule(self):
        calls=[]
        def iptables(*args,**kwargs):
            calls.append(args)
            out=''
            if '-S' in args and 'nat' in args:
                out='-A PREROUTING -i br1 -j PIVAS_DNS\n-A PREROUTING -i br0 -j OTHER_DNS\n'
            if '-S' in args and 'mangle' in args:
                out='-A PREROUTING -i br1 -m set --match-set PIVAS_LIST dst -j PIVAS_MARK\n-A PREROUTING -i br0 -m set --match-set PIVAS_TG dst -j MARK --set-xmark 0xd2000/0xffffffff\n-A PREROUTING -i br0 -j OTHER_MARK\n'
            if args == ('/opt/sbin/ip','rule','show'):
                out='9: from all fwmark 0xd2000/0xd2000 lookup 1002\n10: from all fwmark 0xffffa00 lookup main\n'
            return subprocess.CompletedProcess(args,0,out,'')
        with patch.object(rt,'command',iptables):rt.detach()
        deletes=[a for a in calls if '-D' in a]
        self.assertEqual(len(deletes),3)
        self.assertEqual(sum('br1' in a for a in deletes),2)
        self.assertEqual(sum('PIVAS_TG' in a for a in deletes),1)
        self.assertFalse(any('OTHER_DNS' in a for a in deletes))
        self.assertFalse(any('OTHER_MARK' in a for a in deletes))
        self.assertIn(('/opt/sbin/ipset','destroy','PIVAS_TG'),calls)
        self.assertIn(('/opt/sbin/ip','rule','del','fwmark','0xd2000/0xd2000','lookup','1002','priority','9'),calls)
        self.assertFalse(any(a[:3] == ('/opt/sbin/ip','route','flush') for a in calls))
    def test_remove_proxy_leaves_foreign_proxy22(self):
        calls=[]
        def rci(*args,**kwargs):
            calls.append(args)
            out='{"Proxy21":{"description":"Pivas-proxy-vless"},"Proxy22":{"description":"Personal"}}' if args[-1].endswith('show/interface') else '[{"status":"success"}]'
            return subprocess.CompletedProcess(args,0,out,'')
        with patch.object(rt,'command',rci):rt.remove_proxy()
        payload=json.loads(calls[1][calls[1].index('-d')+1])
        self.assertEqual([i['interface']['name'] for i in payload if 'interface' in i],['Proxy21'])
    def test_prepare_preserves_other_interface_mappings(self):
        self.put('/opt/etc/pivas.paused','')
        self.put('/opt/etc/inface_equals','Wireguard0|personal\n')
        for f in ('xray','dnsmasq','dnscrypt-proxy'):self.put('/opt/sbin/'+f,'binary')
        def rci(*args,**kwargs):
            self.router.calls.append(args)
            return subprocess.CompletedProcess(args,0,'{}' if args[-1].endswith('show/interface') else '[{"status":"success"}]','')
        with patch.object(rt,'command',rci):rt.prepare()
        self.assertIn('Wireguard0|personal',rt.path('/opt/etc/inface_equals').read_text())
        self.assertIn('SETUP_FINISHED=yes',rt.path('/opt/etc/pivas.conf').read_text())
    def test_shell_transfer_calls_dns_apply_without_new_main_entry(self):
        source=(ROOT/'work/orig/data/opt/apps/pivas/bin/libs/vless').read_text()
        def extract(name):
            return re.search(r'^'+name+r'\(\)\s*\{.*?^\}',source,re.M|re.S).group(0)
        self.put('/opt/etc/pivas-slot2.list','')
        trace=self.base/'trace'
        code=extract('pivas_vless_slot_add')+'\n'+extract('_vless_slot2_compact')
        code=code.replace('/opt/apps/pivas/bin/main/dnscrypt_pin','mock_apply')
        code += '\nPIVAS_LIST_FILE="'+str(rt.path('/opt/etc/pivas.list'))+'"\nVLESS_SLOT2_DOMAINS_FILE="'+str(rt.path('/opt/etc/pivas-slot2.list'))+'"\n'
        code += 'mock_apply() { echo apply >> "'+str(trace)+'"; }\n_ensure_t2s_route() { :; }\n'
        code += 'pivas_vless_slot_add 2 example.com\npivas_vless_slot_add 1 example.com\n'
        result=subprocess.run(['/bin/bash'],input=code,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(trace.read_text().splitlines(),['apply','apply'])
        self.assertEqual(rt.path('/opt/etc/pivas-slot2.list').read_text().strip(),'')
    def test_native_dns_failure_is_reported(self):
        self.router.fail = lambda args: args[0]=='curl'
        with self.assertRaises(RuntimeError):rt.native_dns()
    def test_domain_validation_and_normalization(self):
        self.put('/opt/etc/pivas.list','*.Example.com # comment\nпример.рф\n192.0.2.1\n')
        self.assertEqual(rt.entries('/opt/etc/pivas.list'), {'example.com','xn--e1afmkfd.xn--p1ai','192.0.2.1/32'})
        self.put('/opt/etc/pivas.list','a.b"\n')
        with self.assertRaises(ValueError):rt.entries('/opt/etc/pivas.list')
    def test_child_cli_failure_restores_lists(self):
        script = self.base / 'pivas'
        target = rt.path('/opt/etc/pivas.list')
        script.write_text('#!/bin/sh\nprintf "changed.test\\n" > "'+str(target)+'"\nexit 1\n')
        script.chmod(0o755)
        before = target.read_text()
        with rt.lock(): rc = rt.execute_locked([str(script),'vless','1-add','changed.test'])
        self.assertEqual(rc,1)
        self.assertEqual(target.read_text(),before)
    def test_reentrant_lock_does_not_swallow_body_exception(self):
        with rt.lock():
            with self.assertRaises(ValueError):
                with rt.lock(): raise ValueError('expected')
    def test_two_processes_serialize_mutations(self):
        log=self.base/'events'
        code = '''import importlib.util, pathlib, sys, time
s=importlib.util.spec_from_file_location('r',sys.argv[1]);r=importlib.util.module_from_spec(s);s.loader.exec_module(r)
r.BASE=pathlib.Path(sys.argv[2])
with r.lock():
 with open(sys.argv[3],'a') as f:f.write(sys.argv[4]+' start\\n')
 time.sleep(.15)
 with open(sys.argv[3],'a') as f:f.write(sys.argv[4]+' end\\n')
'''
        env=dict(os.environ);env.pop('PIVAS_LOCK_TOKEN',None)
        ps=[subprocess.Popen([sys.executable,'-c',code,str(MODULE),str(self.base),str(log),n],env=env) for n in ('a','b')]
        for p in ps:self.assertEqual(p.wait(timeout=5),0)
        events=log.read_text().splitlines()
        self.assertEqual(events[0].split()[0],events[1].split()[0])
        self.assertEqual(events[2].split()[0],events[3].split()[0])
    def test_web_state_distinguishes_pause_from_xray(self):
        self.put('/opt/etc/pivas.paused','')
        binpath=self.base/'bin';binpath.mkdir()
        for name,body in {'pidof':'echo 1234','ip':"echo 'default dev t2s21'"}.items():
            f=binpath/name;f.write_text('#!/bin/sh\n'+body+'\n');f.chmod(0o755)
        script=(ROOT/'web-src/data/opt/etc/pivas-web/www/cgi-bin/state.sh').read_text().replace('/opt/',str(self.base)+'/opt/')
        proc=self.base/'proc';proc.mkdir();(proc/'uptime').write_text('12345 0\n');(proc/'meminfo').write_text('MemTotal: 102400 kB\nMemAvailable: 51200 kB\n')
        script=script.replace('/proc/',str(proc)+'/')
        script=script.replace(str(self.base)+'/opt/bin/python3',sys.executable).replace(str(self.base)+'/opt/apps/pivas/bin/main/vless_url.py',str(MODULE.parent/'vless_url.py'))
        result=subprocess.run(['/bin/sh'],input=script,text=True,capture_output=True,env=dict(os.environ,PATH=str(binpath)+':/usr/bin:/bin'))
        state=json.loads(result.stdout.split('\n\n',1)[1])
        self.assertTrue(state['xray']);self.assertTrue(state['paused']);self.assertFalse(state['vpn_enabled'])


if __name__ == '__main__': unittest.main()
