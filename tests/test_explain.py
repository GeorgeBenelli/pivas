import json
import unittest
import test_runtime as fixture
import explain
import devices
from test_devices import MAC
rt=fixture.rt
class ExplainTests(unittest.TestCase):
    put=fixture.RuntimeTests.put
    def setUp(self):fixture.RuntimeTests.setUp(self)
    def tearDown(self):fixture.RuntimeTests.tearDown(self)
    def test_specific_child_wins(self):
        r=explain.handle(rt,{'domain':'https://sub.video.example.com/page'})
        self.assertEqual(r['rule'],'video.example.com');self.assertEqual(r['slot'],1)
    def test_device_bypass_overrides_domain_and_custom_dns(self):
        self.put(devices.CONFIG,json.dumps({'version':1,'excluded':{MAC:'Mac'}}))
        r=explain.handle(rt,{'domain':'example.com','mac':MAC})
        self.assertEqual(r['route'],'Обычный маршрут Keenetic');self.assertTrue(r['excluded'])
    def test_pause_overrides_slot(self):
        self.put('/opt/etc/pivas.paused','yes');r=explain.handle(rt,{'domain':'example.com'})
        self.assertTrue(r['paused']);self.assertEqual(r['route'],'Обычный маршрут Keenetic')
    def test_failure_policy_is_explained(self):
        self.put('/opt/tmp/pivas-slot2-dns.down','yes');self.put('/opt/etc/pivas.conf','DNS_VPN_FAILURE=closed\n')
        self.assertIn('заблокирован',explain.handle(rt,{'domain':'example.com'})['dns'])
    def test_static_ip_prefix_and_invalid_domain(self):
        self.put('/opt/etc/pivas.list','192.0.2.0/24\n192.0.2.0/25\n');self.put('/opt/etc/pivas-slot2.list','192.0.2.0/25\n')
        self.assertEqual(explain.handle(rt,{'domain':'192.0.2.10'})['slot'],2)
        with self.assertRaises(ValueError):explain.handle(rt,{'domain':'x;touch /tmp/x'})
    def test_emergency_dns_bypass_does_not_claim_slot_dns(self):
        self.put('/opt/tmp/pivas-dns-watchdog.bypass','yes')
        result=explain.handle(rt,{'domain':'example.com'})
        self.assertIn('аварийно',result['dns']);self.assertEqual(result['route'],'Слот 2')
    def test_ipv6_is_not_claimed_as_managed_by_pivas(self):
        result=explain.handle(rt,{'domain':'2001:db8::1'})
        self.assertIsNone(result['slot']);self.assertIn('Keenetic',result['route']);self.assertIn('IPv4',result['reason'])
if __name__=='__main__':unittest.main()
