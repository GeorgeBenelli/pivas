import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
import test_runtime as fixture

rt=fixture.rt
BACKUP='/opt/etc/.pivas/backup/pivas.json'

def profile(number):
    return dict(tag='slot-'+str(number),protocol='vless',settings={'vnext':[{'address':('amsterdam.example' if number==1 else 'helsinki.example'),'port':443,'users':[{'id':str(number)*8+'-'+str(number)*4+'-'+str(number)*4+'-'+str(number)*4+'-'+str(number)*12,'encryption':'none','flow':'xtls-rprx-vision'}]}]},streamSettings={'network':'tcp' if number==1 else 'xhttp','security':'reality','realitySettings':{'serverName':'sni'+str(number)+'.example','publicKey':'key'+str(number),'shortId':'01' if number==1 else '02'},'sockopt':{'tcpFastOpen':True}},mux={'enabled':number==1})

class SwapTests(unittest.TestCase):
    put=fixture.RuntimeTests.put
    def setUp(self):
        fixture.RuntimeTests.setUp(self)
        self.config={'inbounds':[{'tag':'pivas-dns-1','port':1096}], 'outbounds':[profile(1),{'tag':'direct','protocol':'freedom'},profile(2)],'routing':{'rules':[{'domain':['example.com'],'outboundTag':'slot-2'},{'inboundTag':['pivas-bot'],'outboundTag':'slot-2'}]},'log':{'loglevel':'warning'}}
        self.put(rt.XRAY,json.dumps(self.config))
    def tearDown(self):fixture.RuntimeTests.tearDown(self)
    def test_swap_moves_whole_profiles_keeps_tags_positions_and_routing(self):
        self.assertTrue(rt.swap_slots());actual=json.loads(rt.path(rt.XRAY).read_text())
        expected=copy.deepcopy(self.config)
        expected['outbounds'][0]=dict(profile(2),tag='slot-1');expected['outbounds'][2]=dict(profile(1),tag='slot-2')
        self.assertEqual(actual,expected)
        self.assertEqual(self.router.restarts(),[(rt.SX,'restart')])
    def test_dns_lists_groups_devices_and_pause_flags_unchanged(self):
        for name in ['/opt/etc/pivas-groups.json','/opt/etc/pivas-devices.json','/opt/etc/pivas.paused']:
            self.put(name,'persisted content')
        watched=[rt.SERVERS,rt.DNSMASQ,rt.TOML1,rt.TOML2,rt.IPSETS,'/opt/etc/pivas.list','/opt/etc/pivas-slot2.list','/opt/etc/pivas-groups.json','/opt/etc/pivas-devices.json','/opt/etc/pivas.paused']
        before={n:rt.path(n).read_bytes() if rt.path(n).exists() else None for n in watched}
        rt.swap_slots()
        self.assertEqual(before,{n:rt.path(n).read_bytes() if rt.path(n).exists() else None for n in watched})
        self.assertEqual(self.router.restarts(),[(rt.SX,'restart')])
    def test_backups_compatible_with_existing_rollback_commands(self):
        before=rt.path(rt.XRAY).read_bytes();rt.swap_slots()
        self.assertEqual(rt.path(BACKUP).read_bytes(),before)
        for number in (1,2):
            self.assertEqual(json.loads(rt.path(BACKUP+'.slot'+str(number)).read_text()),{'outbound':profile(number)})
    def test_two_swaps_restore_connections(self):
        rt.swap_slots();rt.swap_slots()
        self.assertEqual(json.loads(rt.path(rt.XRAY).read_text()),self.config)
    def test_equal_profiles_no_restart_or_backup_overwrite(self):
        self.config['outbounds'][2]=dict(profile(1),tag='slot-2');self.put(rt.XRAY,json.dumps(self.config));self.put(BACKUP,'keep')
        before=rt.path(rt.XRAY).read_bytes()
        self.assertFalse(rt.swap_slots());self.assertEqual(self.router.restarts(),[])
        self.assertEqual(rt.path(BACKUP).read_text(),'keep');self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
    def test_empty_placeholder_duplicate_and_non_vless_rejected_without_writes(self):
        for slots in [[],[profile(1)],[profile(1),profile(1),profile(2)],[dict(profile(1),protocol='freedom'),profile(2)],[profile(1),dict(profile(2),settings={'vnext':[{'address':'@SLOT2_ADDR','users':[{}]}]})]]:
            self.put(rt.XRAY,json.dumps({'outbounds':slots}));before=rt.path(rt.XRAY).read_bytes()
            with self.assertRaises(ValueError):rt.swap_slots()
            self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
        self.assertFalse(rt.path(BACKUP).exists());self.assertEqual(self.router.restarts(),[])
    def test_validation_failure_leaves_config_and_backups_untouched(self):
        self.put(BACKUP,'old snapshot');before=rt.path(rt.XRAY).read_bytes()
        self.router.fail=lambda a:a[0]=='/opt/sbin/xray'
        with self.assertRaises(RuntimeError):rt.swap_slots()
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before);self.assertEqual(rt.path(BACKUP).read_text(),'old snapshot')
        self.assertEqual(self.router.restarts(),[])
    def test_restart_failure_restores_both_profiles_and_prior_backups(self):
        self.put(BACKUP,'old snapshot');self.put(BACKUP+'.slot1','old slot1');before=rt.path(rt.XRAY).read_bytes()
        self.router.fail=lambda a:a==(rt.SX,'restart')
        with self.assertRaises(RuntimeError):rt.swap_slots()
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before);self.assertEqual(rt.path(BACKUP).read_text(),'old snapshot')
        self.assertEqual(rt.path(BACKUP+'.slot1').read_text(),'old slot1');self.assertFalse(rt.path(BACKUP+'.slot2').exists())
        self.assertEqual(self.router.restarts(),[(rt.SX,'restart'),(rt.SX,'restart')])
    def test_file_write_failure_does_not_restart_and_restores_config(self):
        original=rt.atomic;before=rt.path(rt.XRAY).read_bytes();failed=False
        def atomic(path,data):
            nonlocal failed
            if path==rt.path(BACKUP) and not failed:
                failed=True;raise OSError('disk full')
            return original(path,data)
        with patch.object(rt,'atomic',atomic),self.assertRaises(OSError):rt.swap_slots()
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before);self.assertFalse(rt.path(BACKUP).exists());self.assertEqual(self.router.restarts(),[])
    def test_successful_restart_command_with_dead_process_also_rolls_back(self):
        before=rt.path(rt.XRAY).read_bytes();failed=False
        def command(*args,**kwargs):
            nonlocal failed
            result=self.router(*args,**kwargs)
            if args==(rt.SX,'restart') and not failed:
                failed=True;self.router.services[rt.SX]=False
            return result
        with patch.object(rt,'command',command),self.assertRaises(RuntimeError):rt.swap_slots()
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
        self.assertTrue(self.router.services[rt.SX]);self.assertFalse(rt.path(BACKUP).exists())
    def test_stopped_xray_stays_stopped(self):
        self.router.services[rt.SX]=False
        rt.swap_slots();self.assertEqual(self.router.restarts(),[])
        self.assertFalse(self.router.services[rt.SX])
    def test_explicit_stop_all_does_not_start_transport(self):
        self.put('/opt/etc/pivas.xray-stopped','yes');self.put('/opt/etc/pivas.paused','yes')
        rt.swap_slots();self.assertEqual(self.router.restarts(),[])
    def test_runtime_command_returns_only_changed_and_invalidates_state_cache(self):
        self.put('/opt/tmp/pivas-web-state.json','stale');output=io.StringIO()
        with redirect_stdout(output):self.assertEqual(rt.main(['swap-slots']),0)
        self.assertEqual(json.loads(output.getvalue()),{'changed':True})
        self.assertFalse(rt.path('/opt/tmp/pivas-web-state.json').exists())

if __name__=='__main__':unittest.main()
