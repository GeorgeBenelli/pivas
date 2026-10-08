"""XHTTP links, shared entry points and legacy Hysteria upgrade regressions."""
import ast
import copy
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import quote
import test_vless_url as fixture
import test_web_api as web
import test_runtime

v = fixture.v
TLS = fixture.GRPC.replace('type=grpc','type=xhttp') + '&path=%2Ftransport%2F&host=edge.example'
REALITY = fixture.REALITY.replace('type=tcp','type=xhttp')

class XhttpTests(unittest.TestCase):
    def test_tls_reality_all_modes_roundtrip(self):
        for base in (TLS, REALITY):
            for mode in ('auto','packet-up','stream-up','stream-one'):
                out = v.parse(base+'&mode='+mode)
                self.assertEqual(out['settings']['vnext'][0]['users'][0]['flow'],'')
                self.assertEqual(out['streamSettings']['xhttpSettings']['mode'],mode)
                self.assertNotIn('tcpSettings',out['streamSettings'])
                self.assertNotIn('grpcSettings',out['streamSettings'])
                self.assertEqual(v.parse(v.serialize(out)),out)
        self.assertEqual(v.parse(TLS.replace('type=xhttp','type=splithttp')),v.parse(TLS))

    def test_extra_preserved_including_download_profile_and_encoded_path(self):
        extra={'headers':{'X-Example':'one & two'},'xmux':{'maxConcurrency':'1-2'},
               'downloadSettings':{'address':'download.example','port':443,'network':'xhttp',
                                   'security':'tls','tlsSettings':{'serverName':'download.example'},
                                   'xhttpSettings':{'path':'/download/','mode':'auto'}}}
        link=TLS.replace('%2Ftransport%2F','%2Fpath%3Fq%3D1%26x%3D2')+'&extra='+quote(json.dumps(extra))
        out=v.parse(link)
        self.assertEqual(out['streamSettings']['xhttpSettings']['extra'],extra)
        self.assertEqual(v.parse(v.serialize(out)),out)

    def test_invalid_combinations_and_json_never_echo_url_or_secret(self):
        cases=[TLS+'&mode=gun',TLS+'&flow=xtls-rprx-vision',REALITY+'&flow=xtls-rprx-vision',
               TLS.replace('security=tls','security=none'),TLS+'&path=relative',TLS+'&host=bad%20host',
               TLS+'&alpn=invalid',TLS+'&path=%2Fother',TLS+'&extra=%7Bbad',
               TLS+'&extra='+quote('[]'),TLS+'&extra='+quote('{"xmux":{},"xmux":{}}'),
               TLS+'&extra='+quote('{"xmux":{"maxConcurrency":NaN}}'),
               TLS+'&extra='+quote('{"headers":{"x":"secret\\nvalue"}}'),
               TLS+'&extra='+quote('{"unsupported":true}'),
               TLS+'&extra='+quote('{"downloadSettings":{"tlsSettings":{"allowInsecure":true}}}')]
        for link in cases:
            with self.subTest(index=cases.index(link)),self.assertRaises(ValueError) as error:v.parse(link)
            self.assertNotIn(fixture.ID,str(error.exception));self.assertNotIn(link,str(error.exception))

    def test_tls_pin_policy_reused_and_quic_probe_for_http3(self):
        pin='ab'*32
        saved=dict(v.parse(TLS+'&pcs='+pin),tag='slot-1')
        with patch.object(v,'certificate_hash') as tcp:
            out=v.prepare(TLS,dict(outbounds=[saved]))
        tcp.assert_not_called();self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],pin)
        with patch.object(v,'quic_certificate',return_value={'status':'private','pin':pin}) as quic,patch.object(v,'certificate_hash') as tcp:
            out=v.prepare(TLS+'&alpn=h3')
        quic.assert_called_once();tcp.assert_not_called()
        self.assertEqual(out['streamSettings']['tlsSettings']['pinnedPeerCertSha256'],pin)

    def test_replace_removes_other_transport_keeps_routes_names_and_socket(self):
        first=dict(v.parse(fixture.REALITY),tag='slot-1',mux={'enabled':True})
        first['streamSettings']['sockopt']={'mark':851969}
        original={'outbounds':[first,dict(v.parse(fixture.GRPC),tag='slot-2')],'routing':{'rules':[]}}
        new=v.replace_slot(original,v.parse(TLS),1)
        self.assertEqual(new['outbounds'][0]['streamSettings']['sockopt'],{'mark':851969})
        self.assertEqual(new['outbounds'][0]['mux'],{'enabled':False})
        self.assertEqual(new['outbounds'][1],original['outbounds'][1]);self.assertEqual(new['routing'],original['routing'])
        self.assertNotIn('realitySettings',new['outbounds'][0]['streamSettings'])
        self.assertEqual(v.state(new)['slot1']['transport'],'XHTTP / TLS')

    def test_web_passes_xhttp_url_without_returning_secret(self):
        req=web.Request('/api/control',dict(action='set',slot=2,url=TLS))
        with patch.object(web.web.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run:req._api()
        self.assertEqual(run.call_args.args[0],['pivas','vless','set-2',TLS])
        self.assertEqual(req.status,200);self.assertNotIn(fixture.ID,req.wfile.getvalue().decode())

    def test_bot_actual_handler_and_prompt(self):
        source=fixture.ROOT/'bot/telegram_bot.py';tree=ast.parse(source.read_text())
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'_handle_vless_set','_vless_set_prompt','_connection_from_message','_send_connection_output'}]
        bot=Mock();run=Mock()
        env=dict(bot=bot,logger=Mock(),io=io,re=__import__('re'),types=SimpleNamespace(Message=object),_is_cancel=lambda x:False,
                 _connections_keyboard=lambda:'connections',_cancel_kb=lambda:'cancel',_slot_label=lambda x:'Slot '+str(x),_run_pivas_and_reply=run)
        exec(compile(ast.Module(body=functions,type_ignores=[]),str(source),'exec'),env)
        message=SimpleNamespace(text=TLS,chat=SimpleNamespace(id=7),from_user=SimpleNamespace(username='owner'))
        env['_handle_vless_set'](message,2)
        self.assertEqual(run.call_args.args[1],['pivas','vless','set-2',TLS])
        env['_vless_set_prompt'](message,1)
        self.assertIn('XHTTP',bot.send_message.call_args.args[1])
        # Telegram text is limited to 4096 characters. Large native extra
        # profiles must reach the exact same CLI without truncation via .txt.
        long_link=TLS+'&extra='+quote(json.dumps({'headers':{'X-Pivas':'a'*4200}}))
        bot.get_file.return_value=SimpleNamespace(file_path='fixture')
        bot.download_file.return_value=(long_link+'\n').encode()
        document=SimpleNamespace(file_id='fixture',file_name='connection.txt',file_size=len(long_link)+1)
        message.document=document;message.text=None
        env['_handle_vless_set'](message,1)
        self.assertEqual(run.call_args.args[1],['pivas','vless','set-1',long_link])
        sent=[]
        bot.send_document.side_effect=lambda chat,doc,**kw:sent.append((doc.read(),kw['visible_file_name']))
        env['_send_connection_output'](7,'Slot 2:\n  URL: '+long_link+'\n')
        self.assertEqual(sent, [((long_link+'\n').encode(),'pivas-slot-2.txt')])
        run.reset_mock();document.file_size=32769
        env['_handle_vless_set'](message,1)
        run.assert_not_called();self.assertTrue(bot.register_next_step_handler.called)


class MigrationTests(unittest.TestCase):
    def legacy(self):
        out=v.parse('hy2://fixture-password@hy.example:443/?sni=tls.example&obfs=salamander&obfs-password=fixture-mask')
        out['streamSettings']['hysteriaSettings'].update(up='50 mbps',down='100 mbps',udphop={'port':'443,5000-5010','interval':5})
        return {'outbounds':[dict(out,tag='slot-1'),dict(v.parse(fixture.REALITY),tag='slot-2')], 'routing':{'rules':[]}}

    def test_migration_preserves_credentials_masks_other_slot_and_routing(self):
        config=self.legacy();original=copy.deepcopy(config);new=v.migrate_config(config)
        stream=new['outbounds'][0]['streamSettings']
        self.assertEqual(stream['hysteriaSettings'],dict(version=2,auth='fixture-password'))
        self.assertEqual(stream['finalmask']['quicParams'],dict(brutalUp='50 mbps',brutalDown='100 mbps',udpHop=dict(ports='443,5000-5010',interval=5)))
        self.assertEqual(stream['finalmask']['udp'],original['outbounds'][0]['streamSettings']['finalmask']['udp'])
        self.assertEqual(config,original);self.assertEqual(new['outbounds'][1],original['outbounds'][1]);self.assertEqual(new['routing'],original['routing'])
        self.assertEqual(v.migrate_config(new),new)
        self.assertEqual(v.parse(v.serialize(original['outbounds'][0])),v.parse(v.serialize(new['outbounds'][0])))
        snapshot={'outbound':original['outbounds'][0]}
        self.assertEqual(v.migrate_config(snapshot)['outbound'],new['outbounds'][0])

    def test_conflicting_settings_fail_without_mutating_original(self):
        config=self.legacy();config['outbounds'][0]['streamSettings']['finalmask']['quicParams']={'brutalUp':'25 mbps'}
        original=copy.deepcopy(config)
        with self.assertRaises(ValueError):v.migrate_config(config)
        self.assertEqual(config,original)

    def test_file_migration_validates_before_write_private_backup_and_idempotence(self):
        rt=test_runtime.rt
        with tempfile.TemporaryDirectory() as tmp,patch.dict(sys.modules,{'runtime':rt}):
            path=Path(tmp)/'pivas.json';raw=json.dumps(self.legacy()).encode();path.write_bytes(raw)
            with patch.object(rt,'lock'),patch.object(rt,'validate_xray',side_effect=RuntimeError('invalid')):
                with self.assertRaises(RuntimeError):v.migrate_file(path)
            self.assertEqual(path.read_bytes(),raw);backup=path.with_name(path.name+'.pre-xray-26.3.27');self.assertFalse(backup.exists())
            with patch.object(rt,'lock'),patch.object(rt,'validate_xray') as validate:
                self.assertTrue(v.migrate_file(path));self.assertFalse(v.migrate_file(path))
            validate.assert_called_once();self.assertEqual(backup.read_bytes(),raw)
            self.assertEqual(backup.stat().st_mode&0o777,0o600);self.assertEqual(path.stat().st_mode&0o777,0o600)
            self.assertEqual(json.loads(path.read_text()),v.migrate_config(self.legacy()))

    def test_routing_and_restore_convert_legacy_profile(self):
        legacy=self.legacy()
        result=test_runtime.rt.routing(legacy,{'example.com'},set())
        self.assertNotIn('up',result['outbounds'][0]['streamSettings']['hysteriaSettings'])
        self.assertEqual(result['outbounds'][0]['streamSettings']['finalmask']['quicParams']['udpHop']['ports'],'443,5000-5010')

if __name__=='__main__':unittest.main()
