import json
import socket
import struct
import subprocess
import unittest
from unittest.mock import patch
import test_runtime as fixture
import devices

rt=fixture.rt
MAC='02:11:22:33:44:55'
OTHER='02:66:77:88:99:AA'


class FakeDNS:
    def __init__(self, queries, reply=True, answer_count=1):
        self.queries=queries;self.reply=reply;self.answer_count=answer_count;self.answers=[]
    def __enter__(self):return self
    def __exit__(self,*_):return False
    def bind(self,_):pass
    def settimeout(self,_):pass
    def sendto(self,packet,address):
        pos=12;labels=[]
        while packet[pos]:
            size=packet[pos];pos+=1;labels.append(packet[pos:pos+size].decode());pos+=size
        self.queries.append(('.'.join(labels),address))
        if self.reply:
            self.answers.append((packet[:2]+struct.pack('!HHHHH',0x8180,1,self.answer_count,0,0),address))
    def recvfrom(self,_):
        if not self.answers:raise socket.timeout()
        return self.answers.pop(0)

class DeviceRouter:
    def __init__(self,base):
        self.base=base;self.calls=[];self.fail_table=None;self.offline=False;self.conntrack_missing=False
        self.hosts=[dict(mac=MAC.lower(),ip='192.168.1.10',name='MacBook',active=True,registered=True,interface={'name':'Home'}),dict(mac=OTHER,ip='192.168.1.20',hostname='Phone',active='yes')]
        self.chains={('nat','PIVAS_DNS'):['-A PIVAS_DNS -p udp --dport 53 -j DNAT --to-destination 127.0.0.1:9753'],('mangle','PIVAS_MARK'):['-A PIVAS_MARK -j CONNMARK --restore-mark','-A PIVAS_MARK -j MARK --set-xmark 0xd1000/0xffffffff']}
    def __call__(self,*args,**kwargs):
        self.calls.append((args,kwargs));out='';code=0
        if args[0]=='curl' and args[-1].endswith('/ip/hotspot'):
            if self.offline:raise subprocess.TimeoutExpired(args,7)
            out=json.dumps({'host':self.hosts})
        elif args[0]==devices.IPT:
            table=args[args.index('-t')+1]
            if args[-1]=='-S':out='\n'.join('-N '+c for t,c in self.chains if t==table)
            else:
                chain=args[-1]
                if (table,chain) not in self.chains:code=1
                else:out='-N '+chain+'\n'+'\n'.join(self.chains[table,chain])+'\n'
        elif args[0]==devices.RESTORE:
            lines=kwargs['input_text'].splitlines();table=lines[0][1:]
            changes=[line for line in lines[1:-1] if line]
            if any(line.startswith(('-F ','-A ')) for line in changes):
                raise RuntimeError('existing router rule cannot be replayed by iptables-restore')
            if '--test' not in args:
                if any('-m comment' in line for line in changes):
                    raise RuntimeError('kernel lacks xt_comment despite a successful --test')
                if self.fail_table==table:self.fail_table=None;raise RuntimeError('commit failed')
                for line in changes:
                    parts=line.split(' ',3);chain=parts[1];current=self.chains[table,chain]
                    if parts[0]=='-D':
                        current.remove('-A '+line[3:])
                    elif parts[0]=='-I':
                        current.insert(int(parts[2])-1,'-A '+chain+' '+parts[3])
                    else:raise AssertionError(line)
        elif args[:4]==('ip','-4','neigh','show'):
            ip=args[-1];host=next((h for h in self.hosts if h.get('ip')==ip),None)
            out=ip+' dev br0 lladdr '+host['mac']+' REACHABLE' if host else ''
        elif args[0]=='conntrack':
            if self.conntrack_missing:raise FileNotFoundError('conntrack')
            out='1 flow entries have been deleted.'
        else:return self.base(*args,**kwargs)
        return subprocess.CompletedProcess(args,code,out,'')

class DeviceTests(unittest.TestCase):
    put=fixture.RuntimeTests.put
    def setUp(self):
        fixture.RuntimeTests.setUp(self)
        self.kernel=DeviceRouter(self.router);self.dm=patch.object(rt,'command',self.kernel);self.dm.start()
        self.dns_queries=[]
        self.dns=patch.object(devices.socket,'socket',side_effect=lambda *_:FakeDNS(self.dns_queries));self.dns.start()
    def tearDown(self):
        self.dns.stop()
        self.dm.stop();fixture.RuntimeTests.tearDown(self)
    def change(self,excluded=True,address=MAC):
        return devices.handle(rt,dict(revision=devices.revision(rt),mac=address,excluded=excluded))
    def test_discovery_uses_hotspot_names_and_never_mutates_keenetic(self):
        state=devices.handle(rt)
        self.assertEqual([h['name'] for h in state['devices']],['MacBook','Phone'])
        self.assertTrue(all(h['online'] for h in state['devices']))
        self.assertEqual(state['devices'][0]['mac'],MAC)
        self.assertFalse(rt.path(devices.CONFIG).exists())
        calls=[a for a,k in self.kernel.calls if a[0]=='curl']
        self.assertTrue(all('--noproxy' in a and '-d' not in a for a in calls))
    def test_exclusion_precedes_dns_and_marking_and_preserves_other_rules(self):
        before={k:list(v) for k,v in self.kernel.chains.items()}
        state=self.change()
        self.assertTrue(next(d for d in state['devices'] if d['mac']==MAC)['excluded'])
        for key in devices.CHAINS:
            expected=devices.rules({MAC:'Mac'},*key)
            self.assertEqual(self.kernel.chains[key],expected+before[key])
        mangle=self.kernel.chains['mangle','PIVAS_MARK']
        self.assertIn('--set-xmark 0x0/0xd1000',mangle[0]);self.assertIn('CONNMARK',mangle[1]);self.assertTrue(mangle[2].endswith('RETURN'))
        self.assertNotIn('-s 192.168', '\n'.join(mangle))
        scripts=[k['input_text'] for a,k in self.kernel.calls if a[0]==devices.RESTORE]
        self.assertTrue(scripts)
        self.assertTrue(all('-F ' not in s and '-A ' not in s for s in scripts))
        self.assertTrue(all('PIVAS_DESTINATION_EXCLUDED' not in s for s in scripts))
        self.assertTrue(all('-m comment' not in s for s in scripts))
    def test_untagged_rules_are_owned_without_comment_module(self):
        for table,chain in devices.CHAINS:
            for rule in devices.rules({MAC:'Mac'},table,chain):
                self.assertTrue(devices.owned(rule))
        self.assertTrue(devices.owned('-A PIVAS_DNS -m mac --mac-source '+MAC+' -m comment --comment '+devices.TAG+' -j RETURN'))
        self.assertFalse(devices.owned('-A PIVAS_DNS -m mac --mac-source '+MAC+' -j DROP'))
        self.assertFalse(devices.owned('-A PIVAS_MARK -m mac --mac-source '+MAC+' -j MARK --set-xmark 0x0/0xffff'))
    def test_ip_change_does_not_change_exclusion_rule(self):
        self.change();before=list(self.kernel.chains['nat','PIVAS_DNS']);self.kernel.hosts[0]['ip']='192.168.1.90'
        state=devices.handle(rt,refresh=True)
        self.assertEqual(state['devices'][0]['ip'],'192.168.1.90');self.assertTrue(state['devices'][0]['excluded'])
        self.assertEqual(self.kernel.chains['nat','PIVAS_DNS'],before)
    def test_reboot_rebuild_uses_saved_mac_without_rci(self):
        self.change();self.kernel.offline=True
        self.kernel.chains['nat','PIVAS_DNS']=['-A PIVAS_DNS -p udp --dport 53 -j DNAT --to-destination 127.0.0.1:9753']
        devices.firewall(rt,devices.load(rt),['nat','PIVAS_DNS'])
        self.assertIn(MAC,self.kernel.chains['nat','PIVAS_DNS'][0])
    def test_disable_exclusion_restores_original_chains(self):
        before={k:list(v) for k,v in self.kernel.chains.items()};self.change();self.change(False)
        self.assertEqual(self.kernel.chains,before);self.assertEqual(devices.load(rt),{})
    def test_second_table_failure_rolls_back_first_and_metadata(self):
        before={k:list(v) for k,v in self.kernel.chains.items()};self.kernel.fail_table='mangle'
        with self.assertRaises(RuntimeError):self.change()
        self.assertEqual(self.kernel.chains,before);self.assertFalse(rt.path(devices.CONFIG).exists())
    def test_config_write_failure_restores_kernel(self):
        before={k:list(v) for k,v in self.kernel.chains.items()}
        with patch.object(rt,'atomic',side_effect=OSError('disk full')),self.assertRaises(OSError):self.change()
        self.assertEqual(self.kernel.chains,before)
    def test_validation_failure_cannot_commit_either_table(self):
        before={k:list(v) for k,v in self.kernel.chains.items()}
        def command(*args,**kwargs):
            if args[0]==devices.RESTORE and '--test' in args and kwargs['input_text'].startswith('*mangle'):
                raise RuntimeError('unsupported match')
            return self.kernel(*args,**kwargs)
        with patch.object(rt,'command',command),self.assertRaises(RuntimeError):self.change()
        self.assertEqual(self.kernel.chains,before);self.assertFalse(rt.path(devices.CONFIG).exists())
    def test_reassigned_ip_does_not_flush_another_devices_connections(self):
        def command(*args,**kwargs):
            if args[:4]==('ip','-4','neigh','show'):
                return subprocess.CompletedProcess(args,0,'192.168.1.10 dev br0 lladdr '+OTHER+' REACHABLE','')
            return self.kernel(*args,**kwargs)
        with patch.object(rt,'command',command):result=self.change()
        self.assertTrue(result['warning']);self.assertIn(MAC,devices.load(rt))
        self.assertFalse(any(a[0]=='conntrack' for a,k in self.kernel.calls))
    def test_read_failure_preserves_saved_exclusions(self):
        self.change();self.kernel.offline=True;state=devices.handle(rt,refresh=True)
        self.assertEqual(len(state['devices']),1);self.assertTrue(state['devices'][0]['excluded']);self.assertTrue(state['warning'])
        self.change(False);self.assertEqual(devices.load(rt),{})
    def test_unseen_device_and_invalid_mac_are_rejected(self):
        for value in ['00:00:00:00:00:00','FF:FF:FF:FF:FF:FF','02:00:00:00:00:01','--help',MAC+'; echo bad']:
            with self.assertRaises(ValueError):self.change(address=value)
    def test_stale_revision_cannot_overwrite_another_change(self):
        rev=devices.revision(rt);self.change()
        with self.assertRaises(ValueError):devices.handle(rt,{'mac':OTHER,'excluded':True,'revision':rev})
    def test_idempotent_save_does_not_flush_connections_again(self):
        self.change();self.kernel.calls.clear();self.change()
        self.assertFalse(any(a[0] in (devices.RESTORE,'conntrack') for a,k in self.kernel.calls))
    def test_conntrack_targets_only_selected_current_ip_and_vpn_dns(self):
        self.change();calls=[a for a,k in self.kernel.calls if a[0]=='conntrack']
        self.assertEqual(len(calls),3)
        self.assertTrue(all(a[a.index('--orig-src')+1]=='192.168.1.10' for a in calls))
        self.assertEqual(calls[0][-2:],('--mark','0xd1000/0xd1000'))
        self.assertEqual(calls[1][-2:],('--dport','53'))
    def test_no_conntrack_reports_reconnect_without_losing_policy(self):
        self.kernel.conntrack_missing=True;result=self.change()
        self.assertTrue(result['warning']);self.assertIn(MAC,devices.load(rt))
    def test_reinclude_warms_only_active_domain_names(self):
        self.put('/opt/etc/pivas.list','browserleaks.com\n192.0.2.0/24\n')
        self.put('/opt/etc/pivas-slot2.list','youtube.com\n')
        self.change();self.dns_queries.clear();result=self.change(False)
        self.assertEqual(self.dns_queries,[('browserleaks.com',('127.0.0.1',9753)),('youtube.com',('127.0.0.1',9753))])
        self.assertFalse(result['warning'])
        self.assertNotIn(MAC,devices.load(rt))
    def test_dns_warm_timeout_warns_but_does_not_keep_device_excluded(self):
        self.change();self.dns.stop();self.dns=patch.object(devices.socket,'socket',side_effect=lambda *_:FakeDNS(self.dns_queries,reply=False));self.dns.start()
        result=self.change(False)
        self.assertIn('DNS-прогрев неполный',result['warning'])
        self.assertNotIn(MAC,devices.load(rt))
    def test_dns_name_without_a_record_is_not_reported_as_warm_failure(self):
        self.change();self.dns.stop();self.dns=patch.object(devices.socket,'socket',side_effect=lambda *_:FakeDNS(self.dns_queries,answer_count=0));self.dns.start()
        result=self.change(False)
        self.assertFalse(result['warning'])
    def test_no_ip_or_router_unavailable_can_remove_saved_exclusion(self):
        self.change();self.kernel.hosts=[];self.change(False)
        self.assertEqual(devices.load(rt),{})
    def test_empty_chains_before_setup_are_not_created(self):
        self.kernel.chains={};self.change();self.assertEqual(self.kernel.chains,{})
        self.assertIn(MAC,devices.load(rt))
    def test_ten_device_limit_rejects_eleventh_without_changing_rules(self):
        self.kernel.hosts=[dict(mac='02:00:00:00:00:%02X'%i,ip='192.168.1.'+str(i+30),name='Device '+str(i),active=True) for i in range(11)]
        for host in self.kernel.hosts[:10]:self.change(address=host['mac'])
        before={k:list(v) for k,v in self.kernel.chains.items()}
        with self.assertRaisesRegex(ValueError,'10 устройств'):self.change(address=self.kernel.hosts[10]['mac'])
        self.assertEqual(len(devices.load(rt)),10);self.assertEqual(self.kernel.chains,before)
    def test_cached_reads_and_explicit_refresh(self):
        devices.handle(rt);self.kernel.calls.clear();devices.handle(rt)
        self.assertFalse(any(a[0]=='curl' for a,k in self.kernel.calls))
        devices.handle(rt,refresh=True)
        self.assertTrue(any(a[0]=='curl' for a,k in self.kernel.calls))
    def test_chain_target_validation(self):
        with self.assertRaises(ValueError):devices.firewall(rt,{MAC:'Mac'},['filter','INPUT'])

if __name__=='__main__':unittest.main()
