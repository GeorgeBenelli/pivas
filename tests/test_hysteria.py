"""Hysteria 2 URL, pinning and cross-protocol slot regressions."""
import ast
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import test_vless_url as vless
import test_swap_slots as swaps
import test_backup as backups
import test_web_api as web

v = vless.v
LINK = 'hysteria2://user%3Ap%40ss%2Fword@hy.example:443/?sni=tls.example'
PIN = 'ab' * 32

class HysteriaTests(unittest.TestCase):
    def test_native_format_and_alias_default_port(self):
        out = v.parse(LINK)
        self.assertEqual(out['protocol'], 'hysteria')
        self.assertEqual(out['settings'], dict(version=2, address='hy.example', port=443))
        stream = out['streamSettings']
        self.assertEqual(stream['network'], 'hysteria')
        self.assertEqual(stream['hysteriaSettings'], dict(version=2, auth='user:p@ss/word'))
        self.assertEqual(stream['tlsSettings'], dict(serverName='tls.example', alpn=['h3']))
        self.assertEqual(v.parse(LINK.replace('hysteria2://', 'hy2://').replace(':443/', '/')), out)
        self.assertTrue(v.configured(out))

    def test_encoded_password_ipv6_salamander_hopping_bandwidth_roundtrip(self):
        for extra in ('', '&insecure=0', '&pinSHA256='+PIN,
                      '&obfs=salamander&obfs-password=mask%26pass&upmbps=50&down=100%20mbps',
                      '&mport=443%2C5000-5010&hopInterval=10s'):
            out = v.parse(LINK+extra)
            self.assertEqual(v.parse(v.serialize(out)), out)
        out = v.parse(LINK.replace('hy.example:443', '[2001:db8::1]:443,5000-5010'))
        self.assertEqual(v.endpoint(out), '[2001:db8::1]:443')
        self.assertEqual(out['streamSettings']['finalmask']['quicParams']['udpHop'], dict(ports='443,5000-5010', interval=30))
        self.assertEqual(v.parse(v.serialize(out)), out)

    def test_invalid_links_reject_without_echoing_secrets(self):
        variants = [LINK.replace(':443', ':0'), LINK.replace(':443', ':65536'),
                    LINK.replace(':443', ':6000-5000'), LINK.replace('hy.example:443', ''),
                    LINK.replace('user%3Ap%40ss%2Fword', 'secret%0Afoo'), LINK+'&sni=bad%0Aname',
                    LINK+'&sni=other', LINK+'&insecure=maybe', LINK+'&insecure=1&allowInsecure=0',
                    LINK+'&obfs=gecko', LINK+'&obfs=salamander', LINK+'&obfs-password=password',
                    LINK+'&pinSHA256=bad', LINK+'&mport=1,2-0', LINK+'&mport=443&hopInterval=1',
                    LINK+'&ech=key', LINK+'&realm=server', LINK+'&up=lots', LINK+'\n',
                    LINK.replace('hysteria2://', 'hysteria://'), LINK.replace('tls.example', '%zz')]
        for link in variants:
            with self.subTest(link_index=variants.index(link)):
                with self.assertRaises(ValueError) as error:
                    v.parse(link)
                self.assertNotIn('user:p@ss', str(error.exception))
                self.assertNotIn(link, str(error.exception))

    def test_private_cert_is_pinned_and_public_cert_keeps_ca(self):
        for status in ('trusted', 'private'):
            with patch.object(v, 'quic_certificate', return_value=dict(status=status,pin=PIN)) as probe:
                out = v.prepare(LINK)
            self.assertEqual(out['streamSettings']['tlsSettings'].get('pinnedPeerCertSha256'), PIN if status=='private' else None)
            self.assertNotIn('allowInsecure', out['streamSettings']['tlsSettings'])
            probe.assert_called_once()

    def test_explicit_insecure_uses_pin_not_removed_xray_option(self):
        with patch.object(v,'quic_certificate',return_value=dict(status='invalid',pin=PIN)):
            out = v.prepare(LINK+'&insecure=1')
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],PIN)
        self.assertNotIn('allowInsecure',out['streamSettings']['tlsSettings'])

    def test_strict_or_supplied_pin_do_not_probe(self):
        with patch.object(v,'quic_certificate') as probe:
            strict=v.prepare(LINK+'&insecure=0')
            pinned=v.prepare(LINK+'&pinSHA256='+PIN)
        self.assertFalse(strict['streamSettings']['tlsSettings']['allowInsecure'])
        self.assertEqual(pinned['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],PIN)
        probe.assert_not_called()

    def test_existing_pin_is_never_refreshed_and_not_reused_across_protocols(self):
        saved=dict(v.parse(LINK+'&pinSHA256='+PIN),tag='slot-1')
        with patch.object(v,'quic_certificate') as probe:
            out=v.prepare(LINK,dict(outbounds=[saved]))
            probe.assert_not_called()
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],PIN)
        with patch.object(v,'quic_certificate',return_value=dict(status='trusted',pin=PIN)) as probe:
            v.prepare(LINK,dict(outbounds=[v.parse(vless.GRPC.replace('vpn.example','hy.example'))]))
            probe.assert_called_once()

    def test_wrong_cert_fails_and_offline_import_stays_strict(self):
        with patch.object(v,'quic_certificate',return_value=dict(status='invalid',pin=PIN)):
            with self.assertRaises(ValueError):v.prepare(LINK)
        with patch.object(v,'quic_certificate',return_value=dict(status='error')):
            out=v.prepare(LINK)
            self.assertNotIn('pinnedPeerCertSha256',out['streamSettings']['tlsSettings'])
            with self.assertRaises(ValueError):v.prepare(LINK+'&insecure=1')

    def test_probe_receives_no_auth_and_timeout_is_bounded(self):
        out=v.parse(LINK+'&obfs=salamander&obfs-password=maskpass')
        with patch.object(subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(dict(status='private',pin=PIN)))) as run:
            self.assertEqual(v.quic_certificate(out)['pin'],PIN)
            req=json.loads(run.call_args.kwargs['input'])
            self.assertEqual(set(req),{'address','port','sni','obfs_password'})
            self.assertNotIn('user:p@ss',run.call_args.kwargs['input'])
            self.assertEqual(run.call_args.kwargs['timeout'],8)
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('probe',8)):
            self.assertEqual(v.quic_certificate(out),dict(status='error'))

    def test_protocol_switch_removes_old_fields_keeps_socket_and_disables_mux(self):
        old=swaps.profile(1)
        config=dict(outbounds=[old,swaps.profile(2)])
        result=v.replace_slot(config,v.parse(LINK),1)
        out=result['outbounds'][0]
        self.assertEqual(out['streamSettings']['sockopt'],old['streamSettings']['sockopt'])
        self.assertEqual(out['mux'],dict(enabled=False))
        self.assertNotIn('vnext',out['settings'])
        self.assertNotIn('realitySettings',out['streamSettings'])
        reverted=v.replace_slot(result,v.parse(vless.GRPC),1)['outbounds'][0]
        self.assertNotIn('hysteriaSettings',reverted['streamSettings'])
        self.assertNotIn('finalmask',reverted['streamSettings'])
        self.assertNotIn('auth',reverted['settings'])
        self.assertEqual(config['outbounds'][0],old)

    def test_state_does_not_mistake_hysteria_for_placeholder(self):
        out=dict(v.parse(LINK),tag='slot-2')
        state=v.state(dict(outbounds=[swaps.profile(1),out]))
        self.assertEqual(state['slot2']['server'],'hy.example:443')
        self.assertEqual(state['slot2']['transport'],'HYSTERIA 2 / QUIC')
        self.assertFalse(state['slot2_same'])
        out['settings']['address']='@SLOT2_ADDRESS'
        self.assertFalse(v.configured(out))

    def test_optional_auth_in_official_uri_scheme_roundtrips(self):
        out=v.parse('hy2://hy.example/?sni=tls.example')
        self.assertEqual(out['streamSettings']['hysteriaSettings']['auth'],'')
        self.assertEqual(v.parse(v.serialize(out)),out)
        self.assertTrue(v.configured(out))

class MixedSwapTests(unittest.TestCase):
    put = swaps.SwapTests.put
    tearDown = swaps.SwapTests.tearDown
    test_two_swaps_restore_connections = swaps.SwapTests.test_two_swaps_restore_connections
    test_restart_failure_restores_both_profiles_and_prior_backups = swaps.SwapTests.test_restart_failure_restores_both_profiles_and_prior_backups
    def setUp(self):
        swaps.SwapTests.setUp(self)
        self.config['outbounds'][2]=dict(v.parse(LINK+'&pinSHA256='+PIN),tag='slot-2')
        self.put(swaps.rt.XRAY,json.dumps(self.config))
    # Run only the relevant inherited transaction regressions with mixed slots.
    def test_swap_moves_whole_profiles_keeps_tags_positions_and_routing(self):
        first,second=copy.deepcopy(self.config['outbounds'][0]),copy.deepcopy(self.config['outbounds'][2])
        self.assertTrue(swaps.rt.swap_slots())
        actual=json.loads(swaps.rt.path(swaps.rt.XRAY).read_text())
        self.assertEqual(actual['outbounds'][0],dict(second,tag='slot-1'))
        self.assertEqual(actual['outbounds'][2],dict(first,tag='slot-2'))
        self.assertEqual(actual['routing'],self.config['routing'])
    def test_backups_compatible_with_existing_rollback_commands(self):
        before=copy.deepcopy(self.config['outbounds'][2])
        swaps.rt.swap_slots()
        self.assertEqual(json.loads(swaps.rt.path(swaps.BACKUP+'.slot2').read_text())['outbound'],before)

class HysteriaBackupTests(unittest.TestCase):
    put = backups.BackupTests.put
    setUp = backups.BackupTests.setUp
    tearDown = backups.BackupTests.tearDown
    def test_hysteria_backup_encrypted_preview_restore(self):
        data=copy.deepcopy(self.original)
        data['slots'][1]=dict(v.parse(LINK+'&obfs=salamander&obfs-password=maskpass&pinSHA256='+PIN),tag='slot-2')
        envelope=backups.backup.seal(data,backups.PASSWORD)
        preview=backups.backup.handle(backups.rt,dict(action='preview',archive=envelope,password=backups.PASSWORD))
        backups.backup.handle(backups.rt,dict(action='restore',archive=envelope,password=backups.PASSWORD,revision=preview['revision']))
        restored=json.loads(backups.rt.path(backups.rt.XRAY).read_text())
        self.assertIn(data['slots'][1],restored['outbounds'])

    def test_restore_old_hysteria_backup_migrates_ports_and_bandwidth(self):
        data=copy.deepcopy(self.original)
        out=dict(v.parse(LINK+'&pinSHA256='+PIN),tag='slot-2')
        out['streamSettings']['hysteriaSettings'].update(up='50 mbps',down='100 mbps',udphop={'port':'443,444','interval':5})
        data['slots'][1]=out
        envelope=backups.backup.seal(data,backups.PASSWORD)
        preview=backups.backup.handle(backups.rt,dict(action='preview',archive=envelope,password=backups.PASSWORD))
        backups.backup.handle(backups.rt,dict(action='restore',archive=envelope,password=backups.PASSWORD,revision=preview['revision']))
        restored=json.loads(backups.rt.path(backups.rt.XRAY).read_text())
        self.assertIn(v.migrate_config({'outbounds':[out]})['outbounds'][0],restored['outbounds'])

class HysteriaBotTests(unittest.TestCase):
    def test_actual_handler_accepts_hy2_alias_and_retries_unsupported_scheme(self):
        source=Path(__file__).resolve().parents[1]/'bot/telegram_bot.py'
        tree=ast.parse(source.read_text())
        names={'_handle_vless_set','_vless_set_prompt','_connection_from_message'}
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        bot=Mock();run=Mock()
        env=dict(bot=bot,logger=Mock(),types=SimpleNamespace(Message=object),_is_cancel=lambda x:x=='cancel',
                 _connections_keyboard=lambda:'connections',_cancel_kb=lambda:'cancel',_slot_label=lambda x:'Slot '+str(x),_run_pivas_and_reply=run)
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),env)
        message=SimpleNamespace(text=LINK.replace('hysteria2://','hy2://'),chat=SimpleNamespace(id=7),from_user=SimpleNamespace(username='owner'))
        env['_handle_vless_set'](message,2)
        self.assertEqual(run.call_args.args[1],['pivas','vless','set-2',message.text])
        message.text='hysteria://secret'
        env['_handle_vless_set'](message,2)
        bot.register_next_step_handler.assert_called_once()
        env['_vless_set_prompt'](message,1)
        self.assertIn('Hysteria 2',bot.send_message.call_args.args[1])


class HysteriaApiTests(unittest.TestCase):
    def test_web_accepts_both_schemes_and_returns_no_auth(self):
        for link in (LINK, LINK.replace('hysteria2://','hy2://')):
            req=web.Request('/api/control',dict(action='set',slot=2,url=link))
            with patch.object(web.web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:
                req._api()
            self.assertEqual(run.call_args.args[0],['pivas','vless','set-2',link])
            self.assertEqual(req.status,200)
            self.assertNotIn('user:p@ss',req.wfile.getvalue().decode())
    def test_diagnostics_redact_urls_auth_and_obfs_password(self):
        import diagnostics
        text=diagnostics.redact(LINK+'\nhy2://secret@host/?obfs-password=maskpass\n'+json.dumps({'auth':'topsecret','password':'maskpass'}))
        for secret in ('user%3A','maskpass','topsecret','secret@host'):
            self.assertNotIn(secret,text)

if __name__=='__main__':unittest.main()
