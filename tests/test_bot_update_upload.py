"""Validate Telegram IPK staging and matching modular/full package variants."""
import ast
import builtins
import io
import logging
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).resolve().parents[1] / 'bot/telegram_bot.py'
WORKER = SOURCE.parent / 'update_worker.py'


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bot = Mock()
        self.bot.message_handler = lambda **_: lambda function: function
        self.bot.get_file.return_value = SimpleNamespace(file_path='package.ipk')
        self.bot.download_file.return_value = b'fake-ipk'
        self.popen = Mock()
        real_mkstemp = tempfile.mkstemp
        fake_temp = SimpleNamespace(mkstemp=lambda **kw: real_mkstemp(prefix=kw['prefix'], suffix=kw['suffix'], dir=self.tmp.name))
        fake_os = SimpleNamespace(makedirs=lambda *a,**k:None, fdopen=os.fdopen,
                                  chmod=os.chmod, unlink=os.unlink, path=os.path)
        self.env = dict(bot=self.bot, types=SimpleNamespace(Message=object),
                        logger=Mock(), os=fake_os, tempfile=fake_temp, shutil=shutil,
                        subprocess=SimpleNamespace(Popen=self.popen, DEVNULL=subprocess.DEVNULL),
                        _group_authorized=lambda user,chat:user.id==7 and chat.type=='private',
                        _service_keyboard=lambda:None, _is_cancel=lambda value:False,
                        _extract_pkg_meta=lambda path:('pivas','all','1.2'),
                        _router_arches=lambda:{'all','mipsel-3.4'},
                        _package_installed=lambda name:False)
        nodes=[node for node in ast.parse(SOURCE.read_text()).body
               if isinstance(node,ast.FunctionDef) and node.name=='handle_pkg_upload']
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.env)
        self.message=SimpleNamespace(text='',document=SimpleNamespace(file_name='pivas.ipk',file_size=8,file_id='file'),
                                     chat=SimpleNamespace(id=7,type='private'),from_user=SimpleNamespace(id=7))
    def tearDown(self):self.tmp.cleanup()
    def invoke(self):
        original_open=builtins.open
        def open_file(path,*args,**kwargs):
            if path=='/opt/etc/telegram4pivas/update_worker.py':
                return original_open(WORKER,*args,**kwargs)
            return original_open(path,*args,**kwargs)
        with patch('builtins.open',side_effect=open_file):
            self.env['handle_pkg_upload'](self.message)
    def text(self):return '\n'.join(call.args[1] for call in self.bot.send_message.call_args_list)
    def test_modular_core_stages_worker_and_preserves_active_state(self):
        self.invoke()
        self.popen.assert_called_once()
        args=self.popen.call_args.args[0]
        self.assertEqual(args[3:6],['pivas','1.2','7'])
        self.assertEqual(args[6],'1')
        self.assertIn('Принял pivas',self.text())
    def test_full_update_allowed_only_on_existing_full_install(self):
        self.env['_extract_pkg_meta']=lambda path:('pivas-full','mipsel-3.4','2.0')
        self.invoke()
        self.popen.assert_not_called();self.assertIn('Тип пакета не совпадает',self.text())
        self.bot.reset_mock();self.env['_package_installed']=lambda name:True
        self.invoke()
        self.popen.assert_called_once()
        self.assertEqual(self.popen.call_args.args[0][3],'pivas-full')
    def test_unauthorized_user_cannot_stage_package(self):
        self.message.from_user.id=42
        self.invoke()
        self.bot.download_file.assert_not_called();self.popen.assert_not_called()


if __name__=='__main__':unittest.main()
