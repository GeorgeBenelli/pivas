import copy
import json
import unittest
from unittest.mock import patch
import test_runtime as fixture
import catalog

rt=fixture.rt

class CatalogTests(unittest.TestCase):
    setUp=fixture.RuntimeTests.setUp
    tearDown=fixture.RuntimeTests.tearDown
    put=fixture.RuntimeTests.put
    def change(self, **kwargs):
        return catalog.handle(rt, dict(revision=catalog.revision(rt), **kwargs))
    def create(self, **kwargs):
        return self.change(action='create',name=kwargs.get('name','Ютуб'),slot=kwargs.get('slot',1),domains=kwargs.get('domains',['youtube.com','googlevideo.com']))['groups'][-1]
    def test_create_adopts_standalone_and_preserves_others(self):
        g=self.create(domains=['example.com','youtu.be'],slot=1)
        result=catalog.view(rt)
        self.assertEqual(set(g['domains']), {'example.com','youtu.be'})
        self.assertNotIn('example.com',rt.entries(catalog.SECOND))
        self.assertNotIn('example.com',[d['domain'] for d in result['standalone']])
        self.assertIn('ordinary.test',rt.entries(catalog.MAIN))
        self.assertEqual(result['drift'],[])
    def test_move_entire_group_updates_dns_and_xray(self):
        g=self.create()
        self.change(action='move',id=g['id'],slot=2)
        self.assertTrue(set(g['domains']) <= rt.entries(catalog.SECOND))
        for d in g['domains']:
            self.assertIn('server=/'+d+'/127.0.0.1#9154',rt.path(rt.SERVERS).read_text())
            self.assertIn({'type':'field','domain':['domain:'+d],'outboundTag':'slot-2'},json.loads(rt.path(rt.XRAY).read_text())['routing']['rules'])
    def test_pause_resume_and_delete_preserve_other_groups(self):
        g=self.create();other=self.create(name='Другие',slot=2,domains=['other.test'])
        self.change(action='toggle',id=g['id'],enabled=False)
        self.assertFalse(set(g['domains'])&rt.entries(catalog.MAIN))
        self.assertEqual(catalog.view(rt)['groups'][0]['domains'],g['domains'])
        self.change(action='move',id=g['id'],slot=2)
        self.assertFalse(set(g['domains'])&rt.entries(catalog.SECOND))
        self.change(action='toggle',id=g['id'],enabled=True)
        self.assertTrue(set(g['domains'])<=rt.entries(catalog.SECOND))
        self.change(action='delete',id=g['id'])
        self.assertFalse(set(g['domains'])&rt.entries(catalog.MAIN))
        self.assertIn('other.test',rt.entries(catalog.MAIN))
        self.assertEqual(catalog.view(rt)['groups'],[other])
    def test_update_replaces_members_and_name_preserving_pause(self):
        g=self.create();self.change(action='toggle',id=g['id'],enabled=False)
        result=self.change(action='update',id=g['id'],name='Видео',slot=2,domains=['new.test'])
        self.assertFalse(result['groups'][0]['enabled'])
        self.assertNotIn('new.test',rt.entries(catalog.MAIN))
        self.assertFalse(set(g['domains'])&rt.entries(catalog.MAIN))
    def test_duplicate_domain_or_name_rejected_without_changes(self):
        g=self.create();before=rt.path(catalog.META).read_bytes()
        for kwargs in [dict(name='ютуб',domains=['other.test']),dict(name='Other',domains=['youtube.com'])]:
            with self.assertRaises(ValueError):self.create(**kwargs)
        self.assertEqual(rt.path(catalog.META).read_bytes(),before)
    def test_optimistic_revision_prevents_lost_update(self):
        stale=catalog.revision(rt);g=self.create()
        with self.assertRaisesRegex(ValueError,'другой вкладке'):
            catalog.handle(rt,dict(action='delete',id=g['id'],revision=stale))
        self.assertEqual(len(catalog.view(rt)['groups']),1)
    def test_validation_and_activation_failures_roll_back_every_file(self):
        g=self.create()
        paths=[catalog.META,catalog.MAIN,catalog.SECOND,rt.XRAY,rt.SERVERS,rt.IPSETS,rt.TOML1,rt.TOML2]
        before={p:rt.path(p).read_bytes() for p in paths}
        for condition in (lambda a:'--test' in a,lambda a:a==(rt.SX,'restart')):
            self.router.fail=condition
            with self.assertRaises(RuntimeError):self.change(action='update',id=g['id'],name='New',slot=2,domains=['new.test'])
            self.assertEqual({p:rt.path(p).read_bytes() for p in paths},before)
    def test_invalid_domains_and_slots_are_rejected(self):
        for value in ['--help','example.com;touch /tmp/x','127.0.0.1','bad..test','evil/$()','2001:b28:f23d::/48','https://core.telegram.org/resources/cidr.txt']:
            with self.assertRaises(ValueError):self.create(domains=[value])
        for slot in [0,3,True,'1']:
            with self.assertRaises(ValueError):self.create(slot=slot)
    def test_idna_and_urls_preserve_www_and_deduplicate(self):
        g=self.create(domains='https://www.Example.org/video\n*.пример.рф, www.example.org')
        self.assertEqual(g['domains'],['www.example.org','xn--e1afmkfd.xn--p1ai'])
    def test_rename_only_does_not_validate_or_restart_network_services(self):
        group=self.create();self.router.calls.clear();before=rt.path(rt.XRAY).read_bytes()
        self.change(action='update',id=group['id'],name='Renamed',slot=group['slot'],domains=group['domains'])
        self.assertEqual(self.router.calls,[]);self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
        self.assertEqual(catalog.view(rt)['groups'][0]['name'],'Renamed')
    def test_legacy_domain_writes_are_guarded_including_aliases(self):
        self.create()
        for args in [['vless','2-add','youtube.com'],['vless','1-del','youtube.com'],['new','youtube.com'],['rm','youtube.com'],['clear'],['import','a.txt']]:
            with self.assertRaises(ValueError):catalog.guard_legacy(rt,args)
        catalog.guard_legacy(rt,['vless','2-add','unrelated.test'])
    def test_external_drift_does_not_silently_destroy_group(self):
        g=self.create();self.put(catalog.MAIN,'ordinary.test\n')
        self.assertTrue(catalog.view(rt)['drift'])
        with self.assertRaisesRegex(ValueError,'вне управления'):self.change(action='move',id=g['id'],slot=2)
    def test_standalone_move_delete_and_group_conflict(self):
        self.change(action='domain-add',slot=2,domains=['ordinary.test'])
        self.assertIn('ordinary.test',rt.entries(catalog.SECOND))
        self.change(action='domain-delete',domains=['ordinary.test'])
        self.assertNotIn('ordinary.test',rt.entries(catalog.MAIN))
        self.create()
        with self.assertRaises(ValueError):self.change(action='domain-delete',domains=['youtube.com'])
    def test_cli_group_lifecycle(self):
        g=catalog.cli(rt,['create','Видео','1','youtube.com'])['groups'][0]
        catalog.cli(rt,['move',g['id'],'2'])
        self.assertIn('youtube.com',rt.entries(catalog.SECOND))
        catalog.cli(rt,['delete',g['id']])
        self.assertEqual(catalog.cli(rt,['list'])['groups'],[])
    def test_ipv4_cidr_group_routes_and_moves_with_network_ownership(self):
        g=self.create(name='Telegram',domains=['91.108.56.1/22','telegram.org'])
        self.assertIn('91.108.56.0/22',g['domains'])
        self.assertIn('91.108.56.0/22',rt.entries(catalog.MAIN))
        self.assertNotIn('server=/91.108.56.0/22/',rt.path(rt.SERVERS).read_text())
        self.change(action='move',id=g['id'],slot=2)
        self.assertIn('91.108.56.0/22',rt.entries(catalog.SECOND))
        rules=json.loads(rt.path(rt.XRAY).read_text())['routing']['rules']
        self.assertIn({'type':'field','ip':['91.108.56.0/22'],'outboundTag':'slot-2'},rules)
        self.change(action='delete',id=g['id'])
        self.assertNotIn('91.108.56.0/22',rt.entries(catalog.MAIN))

if __name__=='__main__':unittest.main()
