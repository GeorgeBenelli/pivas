"""Exercise actual bot handlers without importing router startup code or Telegram IO."""
import ast
import configparser
from contextlib import suppress
import json
import logging
from pathlib import Path
import re
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE=Path(__file__).resolve().parents[1]/'bot/telegram_bot.py'

class Keyboard:
    def __init__(self,**kwargs):self.rows=[]
    def add(self,*buttons):self.rows.append(buttons)

class CancelUpdate:pass

class BotSwapTests(unittest.TestCase):
    def setUp(self):
        self.handlers=[];self.bot=Mock()
        def register(**filters):
            def decorate(fn):self.handlers.append((filters,fn));return fn
            return decorate
        self.bot.message_handler=register
        self.command=Mock(return_value=subprocess.CompletedProcess([],0,'{"changed":true}',''))
        self.env=dict(bot=self.bot,types=SimpleNamespace(Message=object,ReplyKeyboardMarkup=Keyboard,KeyboardButton=lambda text:text),json=json,subprocess=SimpleNamespace(run=self.command),time=SimpleNamespace(sleep=Mock()),logger=Mock(),_vless_swap_lock=threading.Lock(),BaseMiddleware=object,CancelUpdate=CancelUpdate,suppress=suppress,config=configparser.ConfigParser(),CONFIG_PATH='/nonexistent-pivas-tests/config.ini',telegram_bot_config=SimpleNamespace(userid=[7]))
        names={'_connections_keyboard','_send_with_retry','vless_swap','_looks_like_menu_nav','Middleware'}
        tree=ast.parse(SOURCE.read_text())
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='_MENU_BUTTONS' for t in n.targets)]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.env)
        self.message=SimpleNamespace(text='⇄ Поменять ссылки',chat=SimpleNamespace(id=7,type='private'),from_user=SimpleNamespace(id=7,username='owner',full_name='Owner'))
    def invoke(self):self.env['vless_swap'](self.message)
    def texts(self):return '\n'.join(c.args[1] for c in self.bot.send_message.call_args_list)
    def test_button_routes_to_exact_private_handler(self):
        rows=self.env['_connections_keyboard']().rows
        self.assertIn(('⇄ Поменять ссылки',),rows)
        filters,handler=self.handlers[0]
        self.assertEqual(filters['chat_types'],['private'])
        self.assertIsNotNone(re.fullmatch(filters['regexp'],self.message.text))
        self.assertIsNone(re.search(filters['regexp'],'prefix '+self.message.text))
        self.assertTrue(self.env['_looks_like_menu_nav'](self.message.text))
    def test_one_command_announced_before_restart_and_connections_menu_after(self):
        events=[]
        self.bot.send_message.side_effect=lambda *a,**k:events.append('send')
        def run(*args,**kwargs):events.append('command');return subprocess.CompletedProcess([],0,'{"changed":true}','')
        self.command.side_effect=run;self.invoke()
        self.assertEqual(events,['send','command','send'])
        self.command.assert_called_once_with(['pivas','vless','swap'],capture_output=True,text=True)
        self.assertIn('поменялись местами',self.texts())
        self.assertIn(('⇄ Поменять ссылки',),self.bot.send_message.call_args.kwargs['reply_markup'].rows)
        self.assertFalse(self.env['_vless_swap_lock'].locked())
    def test_identical_links_are_reported_as_no_change(self):
        self.command.return_value=subprocess.CompletedProcess([],0,'{"changed":false}','')
        self.invoke();self.assertIn('одинаковые ссылки',self.texts());self.assertNotIn('поменялись местами',self.texts())
    def test_cli_failure_never_echoes_credentials_or_claims_success(self):
        self.command.return_value=subprocess.CompletedProcess([],1,'vless://FAKE_SECRET','password=FAKE_SECRET')
        self.invoke();self.assertIn('Не удалось поменять',self.texts());self.assertNotIn('FAKE_SECRET',self.texts());self.assertNotIn('поменялись местами',self.texts())
    def test_proxy_recovery_retries_only_delivery_not_swap(self):
        self.bot.send_message.side_effect=[None,OSError('proxy down'),OSError('proxy down'),None]
        self.invoke();self.command.assert_called_once();self.assertEqual(self.bot.send_message.call_count,4)
        self.assertFalse(self.env['_vless_swap_lock'].locked())
    def test_failed_delivery_does_not_repeat_command_or_send_false_failure(self):
        self.bot.send_message.side_effect=[None]+[OSError('proxy down')]*6
        self.invoke();self.command.assert_called_once();self.assertFalse(self.env['_vless_swap_lock'].locked())
        self.assertNotIn('Не удалось поменять',self.texts())
    def test_busy_tap_does_not_swap_back(self):
        lock=self.env['_vless_swap_lock'];lock.acquire()
        try:self.invoke()
        finally:lock.release()
        self.command.assert_not_called();self.assertIn('уже выполняется',self.texts())
    def test_malformed_result_and_missing_command_are_not_retried(self):
        for value in ('not JSON','{"changed":"false"}'):
            self.command.reset_mock();self.command.return_value=subprocess.CompletedProcess([],0,value,'')
            self.invoke();self.command.assert_called_once();self.assertFalse(self.env['_vless_swap_lock'].locked())
        self.command.reset_mock();self.command.side_effect=FileNotFoundError('pivas')
        self.invoke();self.command.assert_called_once();self.assertIn('Проверьте состояние',self.texts())
    def test_existing_admin_middleware_rejects_non_admin_swap(self):
        middleware=self.env['Middleware']()
        self.assertIsNone(middleware.pre_process(self.message,{}))
        self.message.from_user.id=42
        self.assertIsInstance(middleware.pre_process(self.message,{}),CancelUpdate)
        self.command.assert_not_called()
    def test_announce_failure_still_runs_command_once(self):
        self.bot.send_message.side_effect=[OSError('temporary'),None]
        self.invoke();self.command.assert_called_once();self.assertIn('поменялись местами',self.texts())

if __name__=='__main__':unittest.main()
