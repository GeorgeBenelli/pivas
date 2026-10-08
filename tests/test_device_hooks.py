"""Execute the shipped shell functions with a fake firewall; never write /opt."""
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest
import test_runtime as fixture
import devices
from test_devices import MAC

DATA=fixture.ROOT/'work/orig/data/opt'

def function(source,name):
    start=source.index(name+'() {')
    return source[start:source.index('\n}',start)+2]

class DeviceHookTests(unittest.TestCase):
    def test_route_rebuild_keeps_device_before_connmark_restore(self):
        source=(DATA/'etc/init.d/S95pivas-routes').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            capture=Path(tmp)/'restore'
            body=function(source,'_ensure_chain').replace('/opt/bin/python3','fake_python').replace('/opt/sbin/iptables-restore','fake_restore')
            prefix='\n'.join(devices.rules({MAC:'Mac'},'mangle','PIVAS_MARK'))
            setup='''CHAIN=PIVAS_MARK; MARK=0xd1000; IPT=fake_iptables
fake_iptables() { return 0; }
fake_python() { printf '%s\\n' %s; }
fake_restore() { cat > %s; }
''' % ("%s",shlex.quote(prefix),shlex.quote(str(capture)))
            result=subprocess.run(['sh','-c',setup+body+'\n_ensure_chain'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            text=capture.read_text()
            self.assertIn('*mangle\n-F PIVAS_MARK\n'+prefix+'\n',text)
            self.assertLess(text.index('--mac-source'),text.index('--restore-mark'))
            self.assertTrue(text.endswith('COMMIT\n'))
    def test_bad_device_config_cannot_flush_live_route_chain(self):
        source=(DATA/'etc/init.d/S95pivas-routes').read_text()
        body=function(source,'_ensure_chain').replace('/opt/bin/python3','fake_python').replace('/opt/sbin/iptables-restore','fake_restore')
        setup='''CHAIN=PIVAS_MARK; MARK=0xd1000; IPT=fake_iptables
fake_iptables() { return 0; }
fake_python() { return 1; }
fake_restore() { echo UNEXPECTED_FLUSH; }
'''
        result=subprocess.run(['sh','-c',setup+body+'\n_ensure_chain'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0);self.assertNotIn('UNEXPECTED_FLUSH',result.stdout)
    def test_route_rebuild_retries_when_keenetic_recreates_chain(self):
        source=(DATA/'etc/init.d/S95pivas-routes').read_text()
        body=function(source,'_ensure_chain').replace('/opt/bin/python3','fake_python').replace('/opt/sbin/iptables-restore','fake_restore')
        with tempfile.TemporaryDirectory() as tmp:
            counter=Path(tmp)/'attempts'
            setup='''CHAIN=PIVAS_MARK; MARK=0xd1000; IPT=fake_iptables
fake_iptables() { return 0; }
fake_python() { return 0; }
sleep() { :; }
fake_restore() {
    cat >/dev/null
    n=$(cat %s 2>/dev/null || echo 0)
    n=$((n+1))
    echo "$n" > %s
    [ "$n" -gt 1 ]
}
''' % (shlex.quote(str(counter)),shlex.quote(str(counter)))
            result=subprocess.run(['sh','-c',setup+body+'\n_ensure_chain'],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(counter.read_text().strip(),'2')
    def test_existing_dns_jump_still_repairs_exclusions(self):
        source=(DATA/'apps/pivas/etc/ndm/ndm').read_text()
        body=function(source,'ip4__dns__add_routing').replace('/opt/etc/pivas.paused','/nonexistent-pivas-test/paused').replace('/opt/tmp/pivas-dns-watchdog.bypass','/nonexistent-pivas-test/bypass')
        setup='''CHAIN_DNS=PIVAS_DNS
save_iptables() { echo '-A PREROUTING -i br0 -j PIVAS_DNS'; }
ip4__dns__create_chain() { echo repaired; }
'''
        result=subprocess.run(['sh','-c',setup+body+'\nip4__dns__add_routing "-i br0"'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'repaired')
    def test_existing_data_and_dns_chains_propagate_bypass_error(self):
        source=(DATA/'apps/pivas/etc/ndm/ndm').read_text()
        for name,args in [('ip4__chain__create_for_data','mangle PIVAS_MARK'),('ip4__dns__create_chain','')]:
            body=function(source,name).replace('/opt/bin/python3','fake_python')
            setup='''CHAIN_DNS=PIVAS_DNS
ip4__chain__is_exist() { return 0; }
save_iptables() { echo '-N PIVAS_DNS'; }
fake_python() { return 1; }
'''
            result=subprocess.run(['sh','-c',setup+body+'\n'+name+' '+args],capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0,name)

if __name__=='__main__':unittest.main()
