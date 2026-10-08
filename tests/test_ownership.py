import ipaddress
import json
import shlex
import subprocess
import unittest
from unittest.mock import patch
import test_runtime as fixture
import ownership as own
rt=fixture.rt

class Kernel:
    def __init__(self,base):self.base=base;self.sets={own.AGG:{},own.LEGACY:{}};self.fail=False;self.calls=[]
    def __call__(self,*args,**kwargs):
        self.calls.append(args)
        if args[0]!=own.IPSET:return self.base(*args,**kwargs)
        out=''
        if args[1:] == ('list','-name'):out='\n'.join(self.sets)
        elif args[1]=='save':
            out='\n'.join('create '+n+' hash:net timeout 86400\n'+'\n'.join('add %s %s timeout %s'%(n,ip,t) for ip,t in ips.items()) for n,ips in self.sets.items())
        elif args[1]=='destroy':self.sets.pop(args[2],None)
        elif args[1]=='restore':
            for line in kwargs['input_text'].splitlines():
                a=shlex.split(line);op,n=a[:2]
                if op=='create':self.sets.setdefault(n,{})
                elif op=='swap':self.sets[n],self.sets[a[2]]=self.sets[a[2]],self.sets[n]
                elif op=='destroy':self.sets.pop(n,None)
                else:
                    ip=str(ipaddress.ip_network(a[2],strict=False))
                    if op=='add':self.sets[n][ip]=int(a[a.index('timeout')+1]) if 'timeout' in a else 0
                    elif op=='del':
                        self.sets[n].pop(ip,None)
                        if self.fail:self.fail=False;raise RuntimeError('partial restore failure')
        return subprocess.CompletedProcess(args,0,out,'')

class OwnershipTests(unittest.TestCase):
    put=fixture.RuntimeTests.put
    def setUp(self):
        fixture.RuntimeTests.setUp(self);self.kernel=Kernel(self.router);self.patch=patch.object(rt,'command',self.kernel);self.patch.start()
    def tearDown(self):self.patch.stop();fixture.RuntimeTests.tearDown(self)
    def populate(self,domain,ips):
        self.kernel.sets[own.name(domain)]={ip+'/32':600 for ip in ips};self.kernel.sets[own.AGG].update(self.kernel.sets[own.name(domain)])
    def test_shared_ip_survives_and_exclusive_ip_is_removed(self):
        self.populate('old.test',['192.0.2.1','192.0.2.2']);self.populate('keep.test',['192.0.2.2'])
        result=own.reconcile(rt,{'domains':['old.test','keep.test'],'networks':[]},{'keep.test'})
        self.assertEqual(set(self.kernel.sets[own.AGG]),{'192.0.2.2/32'});self.assertEqual(result['removed_addresses'],1)
    def test_child_removal_transfers_ownership_to_active_parent(self):
        self.populate('child.example.com',['192.0.2.1']);self.populate('example.com',[])
        own.reconcile(rt,{'domains':['child.example.com','example.com'],'networks':[]},{'example.com'})
        self.assertIn('192.0.2.1/32',self.kernel.sets[own.name('example.com')]);self.assertIn('192.0.2.1/32',self.kernel.sets[own.AGG])
    def test_legacy_entries_are_preserved_for_remaining_ttl(self):
        self.populate('old.test',['192.0.2.1']);self.kernel.sets[own.LEGACY]['192.0.2.1/32']=120
        own.reconcile(rt,{'domains':['old.test'],'networks':[]},set())
        self.assertIn('192.0.2.1/32',self.kernel.sets[own.AGG])
    def test_removed_parent_keeps_ambiguous_child_addresses_with_bounded_lifetime(self):
        self.populate('example.com',['192.0.2.1']);self.populate('video.example.com',[])
        own.reconcile(rt,{'domains':['example.com','video.example.com'],'networks':[]},{'video.example.com'})
        self.assertEqual(self.kernel.sets[own.LEGACY]['192.0.2.1/32'],600)
        self.assertIn('192.0.2.1/32',self.kernel.sets[own.AGG])
        self.assertEqual(self.kernel.sets[own.name('video.example.com')],{})
    def test_removed_static_shared_ip_loses_permanent_timeout(self):
        for owner in (own.LEGACY,own.name('keep.test')):
            with self.subTest(owner=owner):
                self.kernel.sets={own.AGG:{'192.0.2.1/32':0},owner:{'192.0.2.1/32':120}}
                own.reconcile(rt,{'domains':['keep.test'],'networks':['192.0.2.1/32']},{'keep.test'})
                self.assertEqual(self.kernel.sets[own.AGG]['192.0.2.1/32'],120)
    def test_new_static_ip_is_permanent_even_when_previously_learned(self):
        self.populate('keep.test',['192.0.2.1'])
        own.reconcile(rt,{'domains':['keep.test'],'networks':[]},{'keep.test','192.0.2.1/32'})
        self.assertEqual(self.kernel.sets[own.AGG]['192.0.2.1/32'],0)
    def test_cleanup_timeout_does_not_report_committed_transaction_as_failed(self):
        with patch.object(rt,'command',side_effect=subprocess.TimeoutExpired('ipset',1)):
            own.finish(rt,{'domains':['old.test']},set(),{})
    def test_failed_kernel_rollback_still_attempts_service_recovery(self):
        rt.apply();self.populate('ordinary.test',['192.0.2.1'])
        self.router.fail=lambda a:a==(rt.SD,'start')
        with patch.object(own,'undo',side_effect=RuntimeError('kernel unavailable')):
            with self.assertRaisesRegex(RuntimeError,'неполный откат'):
                rt.apply(list_values=(rt.entries('/opt/etc/pivas.list')-{'ordinary.test'},{'example.com'}))
        self.assertTrue(self.router.services[rt.SD]);self.assertTrue(self.router.services[rt.SX])
    def test_partial_ipset_failure_restores_aggregate(self):
        self.populate('old.test',['192.0.2.1','192.0.2.2']);before=dict(self.kernel.sets[own.AGG]);self.kernel.fail=True
        with self.assertRaises(RuntimeError):own.reconcile(rt,{'domains':['old.test'],'networks':[]},set())
        self.assertEqual(self.kernel.sets[own.AGG],before)
    def test_static_network_removal_preserves_new_subnet(self):
        self.kernel.sets[own.AGG]={'192.0.2.0/24':0}
        own.reconcile(rt,{'domains':[],'networks':['192.0.2.0/24']},{'192.0.2.0/25'})
        self.assertEqual(self.kernel.sets[own.AGG],{'192.0.2.0/25':0})
    def test_prepare_is_batched_and_steady_state_does_not_dump_members(self):
        own.prepare(rt,{'first.test','second.test'});self.kernel.calls.clear();own.prepare(rt,{'first.test','second.test'})
        self.assertEqual(self.kernel.calls,[(own.IPSET,'list','-name')])
    def test_bootstrap_keeps_unknown_ips_with_remaining_timeout(self):
        self.kernel.sets={own.AGG:{'192.0.2.1/32':90}}
        own.prepare(rt,{'first.test'})
        self.assertEqual(self.kernel.sets[own.LEGACY],{'192.0.2.1/32':90})
    def test_kernel_capacity_is_checked_before_creating_sets(self):
        self.put('/sys/module/ip_set/parameters/max_sets','3')
        with self.assertRaisesRegex(ValueError,'лимит ядра'):
            own.prepare(rt,{'new.test'})
        self.assertEqual(self.kernel.sets,{own.AGG:{},own.LEGACY:{}})
    def test_zero_max_sets_uses_conservative_compiled_default(self):
        self.put('/sys/module/ip_set/parameters/max_sets','0')
        self.kernel.sets.update({'other%03d'%i:{} for i in range(253)})
        with self.assertRaisesRegex(ValueError,'лимит ядра 256'):
            own.prepare(rt,{'new.test'})
    def test_lost_kernel_sets_are_rebuilt_with_static_rules(self):
        values=rt.entries('/opt/etc/pivas.list')|{'192.0.2.0/24'}
        self.put('/opt/etc/pivas.list','\n'.join(sorted(values))+'\n');rt.apply()
        self.kernel.sets={};rt.apply()
        self.assertEqual(self.kernel.sets[own.AGG]['192.0.2.0/24'],0)
    def test_failed_dns_start_restores_ips_and_config(self):
        rt.apply();self.populate('ordinary.test',['192.0.2.1']);before=rt.path(rt.XRAY).read_bytes()
        self.router.fail=lambda a:a==(rt.SD,'start')
        values=rt.entries('/opt/etc/pivas.list')-{'ordinary.test'}
        with self.assertRaises(RuntimeError):rt.apply(list_values=(values,{'example.com'}))
        self.assertIn('192.0.2.1/32',self.kernel.sets[own.AGG]);self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
        self.assertIn(own.name('ordinary.test'),self.kernel.sets)
    def test_paused_removal_is_applied_on_resume(self):
        rt.apply();self.populate('ordinary.test',['192.0.2.1']);self.put('/opt/etc/pivas.paused','yes')
        self.put('/opt/etc/pivas.list','example.com\nvideo.example.com\n');rt.apply()
        self.assertIn('ordinary.test',own.load(rt)['domains'])
        rt.path('/opt/etc/pivas.paused').unlink();rt.apply()
        self.assertNotIn('192.0.2.1/32',self.kernel.sets[own.AGG])
    def test_retired_shared_domain_uses_direct_but_active_child_keeps_slot(self):
        self.put(own.STATE,json.dumps({'domains':['example.com','video.example.com'],'networks':[]}))
        retired=own.retired(rt,{'video.example.com'});self.assertEqual(set(retired),{'example.com'})
        config=rt.routing({'outbounds':[{'tag':'slot-1'},{'tag':'slot-2'}]}, {'video.example.com'},{'video.example.com'},retired)
        rules=[r for r in config['routing']['rules'] if 'domain' in r]
        self.assertEqual([(r['domain'][0],r['outboundTag']) for r in rules],[('domain:video.example.com','slot-2'),('domain:example.com','pivas-direct')])
    def test_expired_retired_domain_drops_from_next_config(self):
        self.put(own.STATE,json.dumps({'domains':[],'networks':[]}))
        self.put(own.RETIRED,json.dumps({'expired.test':100,'recent.test':1000}))
        with patch.object(own.time,'time',return_value=500):
            self.assertEqual(set(own.retired(rt,set())),{'recent.test'})

if __name__=='__main__':unittest.main()
