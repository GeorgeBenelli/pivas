"""Transport import/export and replacement regressions, without live credentials."""
import copy
import importlib.util
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import ssl
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'work/orig/data/opt/apps/pivas/bin/main/vless_url.py'
spec = importlib.util.spec_from_file_location('vless_url', MODULE)
v = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v)
ID = '00000000-0000-0000-0000-000000000001'
GRPC = f'vless://{ID}@vpn.example:443?type=grpc&security=tls&fp=edge&sni=tls.example&serviceName=x5.lk.v1.AccountService'
REALITY = f'vless://{ID}@vpn.example:443?type=tcp&security=reality&pbk=example-key&sid=abcdef&spx=%2Fhello%3Fx%3D1'


class VlessTests(unittest.TestCase):
    def test_grpc_tls_without_reality_keys_or_flow(self):
        out = v.parse(GRPC)
        self.assertEqual(out['settings']['vnext'][0]['users'][0]['flow'], '')
        stream = out['streamSettings']
        self.assertEqual(stream['grpcSettings'], {'serviceName': 'x5.lk.v1.AccountService', 'multiMode': False})
        self.assertEqual(stream['tlsSettings'], {'serverName':'tls.example', 'alpn':['h2'], 'fingerprint':'edge'})
        self.assertNotIn('realitySettings', stream)

    def test_grpc_parameters_round_trip(self):
        out = v.parse(GRPC + '&mode=multi&authority=custom.example&alpn=h2%2Chttp%2F1.1&allowInsecure=1')
        self.assertEqual(v.parse(v.serialize(out)), out)
        self.assertTrue(out['streamSettings']['grpcSettings']['multiMode'])

    def test_encoded_service_name_round_trip(self):
        link = GRPC.replace('x5.lk.v1.AccountService', '%2Fcustom%2Fservice%3Ffoo%3D1%26bar%3D2%2Bplus')
        out = v.parse(link)
        self.assertEqual(out['streamSettings']['grpcSettings']['serviceName'], '/custom/service?foo=1&bar=2+plus')
        self.assertEqual(v.parse(v.serialize(out)), out)

    def test_reality_defaults_and_round_trip(self):
        for extra in ('', '&flow='):
            out = v.parse(REALITY + extra)
            self.assertEqual(v.parse(v.serialize(out)), out)
        self.assertEqual(v.parse(REALITY)['settings']['vnext'][0]['users'][0]['flow'], 'xtls-rprx-vision')

    def test_ipv6_endpoint_round_trip(self):
        out = v.parse(GRPC.replace('vpn.example', '[2001:db8::1]'))
        self.assertEqual(v.endpoint(out), '[2001:db8::1]:443')
        self.assertEqual(v.parse(v.serialize(out)), out)

    def test_unsupported_and_invalid_links_rejected(self):
        links = [GRPC+'&flow=xtls-rprx-vision', GRPC+'&alpn=http%2F1.1', GRPC+'&mode=bad',
                 GRPC+'&allowInsecure=maybe', GRPC+'&serviceName=other', GRPC+'&sni=x%0Ay',
                 GRPC.replace(':443?', ':0?'), GRPC.replace(':443?', ':65536?'), GRPC.replace(':443?', '?'),
                 GRPC.replace('type=grpc', 'type=xhttp')+'&mode=gun', GRPC.replace('security=tls', 'security=reality'),
                 GRPC.replace('serviceName=', 'serviceName=%zz'), REALITY+'&sid=abc',
                 REALITY.replace('&pbk=example-key', ''), GRPC+'&mode=gun&multiMode=true']
        for link in links:
            with self.subTest(link=link), self.assertRaises(ValueError): v.parse(link)

    def test_switch_transport_removes_old_fields_preserves_sockopt_and_other_slot(self):
        first = dict(v.parse(REALITY), tag='slot-1', mux={'enabled':True})
        first['streamSettings']['sockopt'] = {'mark':255}
        config = {'outbounds':[first, dict(copy.deepcopy(first), tag='slot-2')], 'routing':{'rules':[]}}
        grpc = v.replace_slot(config, v.parse(GRPC), 1)
        self.assertEqual(grpc['outbounds'][1], config['outbounds'][1])
        self.assertEqual(grpc['routing'], config['routing'])
        self.assertEqual(grpc['outbounds'][0]['streamSettings']['sockopt'], {'mark':255})
        self.assertFalse(grpc['outbounds'][0]['mux']['enabled'])
        self.assertNotIn('realitySettings', grpc['outbounds'][0]['streamSettings'])
        self.assertNotIn('tcpSettings', grpc['outbounds'][0]['streamSettings'])
        restored = v.replace_slot(grpc, v.parse(REALITY), 1)
        self.assertNotIn('grpcSettings', restored['outbounds'][0]['streamSettings'])
        self.assertNotIn('tlsSettings', restored['outbounds'][0]['streamSettings'])
        self.assertEqual(config['outbounds'][0], first)

    def test_shell_state_does_not_execute_parameter_contents(self):
        out = v.parse(GRPC + '&authority=%27%3B%24%28printf%20INJECTED%29%3B%27')
        config = {'outbounds':[dict(out, tag='slot-1')]}
        script = v.state_shell(config) + '\nprintf "%s" "$slot1_url"\n'
        result = subprocess.run(['sh'], input=script, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, v.serialize(dict(out, tag='slot-1')))

    def test_public_certificate_preserves_ca_verification(self):
        with patch.object(v, 'certificate_hash', return_value='ab'*32) as probe:
            out = v.prepare(GRPC)
        self.assertNotIn('allowInsecure', out['streamSettings']['tlsSettings'])
        self.assertNotIn('pinnedPeerCertSha256', out['streamSettings']['tlsSettings'])
        self.assertEqual(probe.call_count, 1)

    def test_private_certificate_is_pinned_without_native_insecure(self):
        error = ssl.SSLCertVerificationError('private CA'); error.verify_code = 20
        with patch.object(v, 'certificate_hash', side_effect=[error, 'ab'*32]) as probe:
            out = v.prepare(GRPC)
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'], 'ab'*32)
        self.assertNotIn('allowInsecure', out['streamSettings']['tlsSettings'])
        self.assertEqual(probe.call_args.kwargs, {'verified':False})
        self.assertEqual(v.parse(v.serialize(out)), out)

    def test_legacy_insecure_becomes_pin_not_removed_xray_option(self):
        for flag in ('allowInsecure=1','insecure=true'):
            with patch.object(v, 'certificate_hash', return_value='ab'*32) as probe:
                out = v.prepare(GRPC+'&'+flag)
            self.assertEqual(probe.call_count,1)
            self.assertFalse(probe.call_args.kwargs['verified'])
            self.assertFalse(out['streamSettings']['tlsSettings'].get('allowInsecure',False))
            self.assertEqual(v.parse(v.serialize(out)),out)
            self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],'ab'*32)

    def test_strict_flag_and_explicit_pin_do_not_probe(self):
        for flag in ('allowInsecure=0','insecure=false','pcs='+('ab'*32)):
            with patch.object(v,'certificate_hash') as probe:
                out=v.prepare(GRPC+'&'+flag)
            probe.assert_not_called()
            self.assertFalse(out['streamSettings']['tlsSettings'].get('allowInsecure',False))
            self.assertEqual(v.parse(v.serialize(out)),out)

    def test_remembered_pin_reused_without_network_or_silent_replacement(self):
        known=dict(v.parse(GRPC+'&pcs='+('ab'*32)),tag='slot-2')
        with patch.object(v,'certificate_hash',return_value='cd'*32) as probe:
            out=v.prepare(GRPC,{'outbounds':[known]})
        probe.assert_not_called()
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],'ab'*32)
        self.assertEqual(known['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],'ab'*32)

    def test_other_server_pin_cannot_be_reused(self):
        known=dict(v.parse(GRPC.replace('vpn.example','other.example')+'&pcs='+('ab'*32)),tag='slot-1')
        with patch.object(v,'certificate_hash',return_value='cd'*32) as probe:
            out=v.prepare(GRPC,{'outbounds':[known]})
        self.assertEqual(probe.call_count,1)
        self.assertNotIn('pinnedPeerCertSha256',out['streamSettings']['tlsSettings'])

    def test_wrong_name_or_expired_certificate_not_automatically_pinned(self):
        for code in (10,62):
            error=ssl.SSLCertVerificationError('expired or wrong name');error.verify_code=code
            with patch.object(v,'certificate_hash',side_effect=error) as probe:
                with self.assertRaises(ValueError):v.prepare(GRPC)
            self.assertEqual(probe.call_count,1)

    def test_offline_default_keeps_ca_but_legacy_insecure_requires_pin(self):
        with patch.object(v,'certificate_hash',side_effect=OSError('offline')):
            out=v.prepare(GRPC)
            with self.assertRaises(ValueError):v.prepare(GRPC+'&allowInsecure=1')
        self.assertNotIn('pinnedPeerCertSha256',out['streamSettings']['tlsSettings'])

    def test_pin_validation_and_legacy_flag_conflicts(self):
        for extra in ('&pcs=bad','&pcs='+('ab'*31),'&allowInsecure=1&insecure=0'):
            with self.subTest(extra=extra),self.assertRaises(ValueError):v.parse(GRPC+extra)
        pin=':'.join(['AB']*32)
        out=v.parse(GRPC+'&pcs='+pin+'&vcn=tls.example')
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],'ab'*32)
        self.assertEqual(v.parse(v.serialize(out)),out)

    def test_real_shell_import_uses_shared_parser(self):
        source = (ROOT/'work/orig/data/opt/apps/pivas/bin/libs/vless').read_text()
        parser = source[source.index('_vless_parse_url() {'):source.index('_vless_init_config_if_needed() {')]
        importer = source[source.index('vless_link_parse() {'):source.index('vless_link_parse() {')+source[source.index('vless_link_parse() {'):].index('\n}\n')+3]
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)/'config.json'
            cfg.write_text(json.dumps({'outbounds':[dict(v.parse(REALITY),tag='slot-1'),dict(v.parse(REALITY),tag='slot-2')]}))
            script = '_vless_invalid(){ echo "$1" >&2; }; error(){ echo "$1" >&2; }; _vless_init_config_if_needed(){ :; }; _vless_ensure_placeholder_slots_mirrored(){ :; };\n'+parser+importer+'\nvless_link_parse "$1" "$2" 1\n'
            script = script.replace('/opt/bin/python3',shlex.quote(sys.executable)).replace('/opt/apps/pivas/bin/main/vless_url.py',shlex.quote(str(MODULE)))
            result = subprocess.run(['sh','-c',script,'test',GRPC+'&allowInsecure=0',str(cfg)],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            out = json.loads(cfg.read_text())['outbounds'][0]
            self.assertEqual(out['streamSettings'],v.parse(GRPC+'&allowInsecure=0')['streamSettings'])
            before = cfg.read_bytes()
            failed = subprocess.run(['sh','-c',script,'test',GRPC+'&flow=xtls-rprx-vision',str(cfg)],text=True,capture_output=True)
            self.assertNotEqual(failed.returncode,0)
            self.assertEqual(cfg.read_bytes(),before)
            self.assertNotIn(ID,failed.stderr)


if __name__ == '__main__': unittest.main()
