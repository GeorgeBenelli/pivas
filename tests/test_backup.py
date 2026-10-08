import copy
import json
import unittest
from unittest.mock import patch
import test_runtime as fixture
from test_swap_slots import profile
from test_ownership import Kernel
from test_devices import DeviceRouter, MAC
import backup
import catalog
import devices
import slots
rt=fixture.rt
PASSWORD='a long testing password'

class BackupTests(unittest.TestCase):
    put=fixture.RuntimeTests.put
    def setUp(self):
        fixture.RuntimeTests.setUp(self)
        self.put(rt.XRAY,json.dumps({'outbounds':[profile(1),profile(2)],'inbounds':[],'routing':{'rules':[]}}))
        self.kernel=Kernel(self.router);self.dk=DeviceRouter(self.kernel);self.patch=patch.object(rt,'command',self.dk);self.patch.start()
        self.original=backup.export(rt)
    def tearDown(self):self.patch.stop();fixture.RuntimeTests.tearDown(self)
    def test_authenticated_encryption_roundtrip_hides_secrets(self):
        envelope=backup.seal(self.original,PASSWORD)
        self.assertNotIn('amsterdam',json.dumps(envelope));self.assertNotIn(PASSWORD,json.dumps(envelope))
        self.assertEqual(backup.unseal(envelope,PASSWORD),self.original)
        self.assertNotEqual(envelope,backup.seal(self.original,PASSWORD))
    def test_wrong_password_and_tampering_are_rejected_before_decrypt(self):
        envelope=backup.seal(self.original,PASSWORD)
        with patch.object(backup,'crypt') as crypt:
            with self.assertRaises(ValueError):backup.unseal(envelope,'another long password')
            envelope['ciphertext']='A'+envelope['ciphertext'][1:]
            with self.assertRaises(ValueError):backup.unseal(envelope,PASSWORD)
            crypt.assert_not_called()
    def test_preview_does_not_write_settings_and_returns_no_credentials(self):
        envelope=backup.seal(self.original,PASSWORD);before=rt.path(rt.XRAY).read_bytes()
        result=backup.handle(rt,dict(action='preview',archive=envelope,password=PASSWORD))
        self.assertEqual(result['summary']['slots'],2);self.assertNotIn('amsterdam',json.dumps(result))
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before);self.assertEqual(self.router.restarts(),[])
    def test_restore_changes_slots_and_exclusions_and_keeps_pause(self):
        data=copy.deepcopy(self.original);data['slots'][0]['settings']['vnext'][0]['address']='new.example';data['devices']={MAC:'Mac'}
        envelope=backup.seal(data,PASSWORD);self.put('/opt/etc/pivas.paused','yes')
        preview=backup.handle(rt,dict(action='preview',archive=envelope,password=PASSWORD))
        result=backup.handle(rt,dict(action='restore',archive=envelope,password=PASSWORD,revision=preview['revision']))
        self.assertTrue(result['restored']);self.assertIn('new.example',rt.path(rt.XRAY).read_text());self.assertIn(MAC,devices.load(rt));self.assertTrue(rt.path('/opt/etc/pivas.paused').exists())
        self.assertIn(MAC,'\n'.join(self.dk.chains['nat','PIVAS_DNS']))
        saved=json.loads(rt.path('/opt/etc/pivas-backup/before-restore.pivas').read_text())
        self.assertEqual(backup.unseal(saved,PASSWORD),self.original)
    def test_stale_restore_is_rejected(self):
        envelope=backup.seal(self.original,PASSWORD);rev=backup.revision(rt);self.put('/opt/etc/pivas.list','changed.example\n')
        with self.assertRaises(ValueError):backup.handle(rt,dict(action='restore',archive=envelope,password=PASSWORD,revision=rev))
    def test_names_roundtrip_and_legacy_copy_defaults(self):
        slots.cli(rt,['name','1','Германия'])
        data=backup.export(rt)
        self.assertEqual(data['slot_names']['1'],'Германия')
        files,*_=backup.validate(rt,backup.unseal(backup.seal(data,PASSWORD),PASSWORD))
        self.assertEqual(json.loads(files[slots.CONFIG])['names']['1'],'Германия')
        data.pop('slot_names')
        files,*_=backup.validate(rt,data)
        self.assertEqual(json.loads(files[slots.CONFIG])['names'],slots.DEFAULTS)
    def test_names_change_invalidates_restore_preview(self):
        rev=backup.revision(rt);slots.cli(rt,['name','2','Резерв'])
        self.assertNotEqual(backup.revision(rt),rev)
    def test_backup_rejects_invalid_slot_names(self):
        for names in ({'1':'one'}, {'1':'one','2':'x'*33}, {'1':'one','2':'bad\nname'}):
            data=copy.deepcopy(self.original);data['slot_names']=names
            with self.assertRaises(ValueError):backup.validate(rt,data)
    def test_validation_refuses_unknown_fields_conflicts_and_more_than_ten_devices(self):
        variants=[]
        d=copy.deepcopy(self.original);d['../../file']='bad';variants.append(d)
        d=copy.deepcopy(self.original);d['dns']['LAN_INTERFACE']='evil';variants.append(d)
        d=copy.deepcopy(self.original);d['dns']['DNS_PROVIDER']='1.1.1.1\nserver=evil';variants.append(d)
        d=copy.deepcopy(self.original);d['second']=['outside.test'];variants.append(d)
        d=copy.deepcopy(self.original);d['devices']={'02:00:00:00:00:%02X'%i:'device' for i in range(11)};variants.append(d)
        before=rt.path(rt.XRAY).read_bytes()
        for d in variants:
            with self.assertRaises(ValueError):backup.validate(rt,d)
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
    def test_firewall_failure_rolls_back_files_dns_and_device_policy(self):
        rt.apply();before={n:rt.path(n).read_bytes() for n in (rt.XRAY,rt.SERVERS,rt.DNSMASQ)}
        data=backup.export(rt);data['devices']={MAC:'Mac'};data['slots'][0]['settings']['vnext'][0]['address']='changed.example'
        envelope=backup.seal(data,PASSWORD);self.dk.fail_table='mangle'
        with self.assertRaises(RuntimeError):backup.handle(rt,dict(action='restore',archive=envelope,password=PASSWORD,revision=backup.revision(rt)))
        self.assertEqual({n:rt.path(n).read_bytes() for n in before},before);self.assertEqual(devices.load(rt),{})
        self.assertNotIn(MAC,'\n'.join(self.dk.chains['nat','PIVAS_DNS']))

if __name__=='__main__':unittest.main()
