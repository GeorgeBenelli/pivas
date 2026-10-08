"""Exercise Telegram group controls without contacting Telegram or a router."""
import ast
import copy
import json
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'bot/telegram_bot.py'
GROUP_ID = '0123456789abcdef'


class Keyboard:
    def __init__(self, **kwargs): self.rows = []
    def add(self, *buttons): self.rows.append(buttons)
    def row(self, *buttons): self.rows.append(buttons)


class Button:
    def __init__(self, text, callback_data):
        self.text, self.callback_data = text, callback_data


class GroupBotTests(unittest.TestCase):
    def setUp(self):
        self.bot = Mock()
        self.bot.message_handler = lambda **filters: lambda fn: fn
        self.bot.callback_query_handler = lambda **filters: lambda fn: fn
        self.bot.send_message.side_effect = lambda chat_id, text, **kw: SimpleNamespace(chat=SimpleNamespace(id=chat_id), message_id=99)
        self.types = SimpleNamespace(Message=object, KeyboardButton=lambda x:x,
                                     ReplyKeyboardMarkup=Keyboard, InlineKeyboardMarkup=Keyboard,
                                     InlineKeyboardButton=Button)
        self.env = dict(bot=self.bot, types=self.types, json=json, re=re, subprocess=subprocess,
                        logger=Mock(), _group_drafts={}, _GROUP_PAGE_SIZE=8,
                        _GROUP_DOMAIN_PAGE_SIZE=10, _GROUP_RUNTIME='/opt/apps/pivas/bin/main/runtime.py',
                        _DOMAIN_FILE_MAX=512*1024, _send_with_retry=self.bot.send_message,
                        _slot_labels=lambda: {'1':'Слот 1','2':'Слот 2'},
                        _group_authorized=lambda user, chat: chat.type == 'private' and user.id == 7)
        selected = {'GroupMenuError','_hosts_keyboard','_group_catalog','_group_button','_group_find','_slot_label',
                    '_group_index','_group_detail','_group_show','_group_mutate','_group_stale',
                    '_group_prompt','_group_cancel_step','_group_domains_from_message','_group_domain_key',
                    'group_menu','_group_create_name','_group_create_domains','_group_edit_text',
                    'group_callback','_cancel_kb','_is_cancel','_looks_like_menu_nav'}
        tree = ast.parse(SOURCE.read_text())
        nodes = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in selected
                 or isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id == '_MENU_BUTTONS' for t in n.targets)]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(SOURCE),'exec'),self.env)
        self.original_catalog = self.env['_group_catalog']
        self.state = {'revision':'a'*64, 'groups':[], 'standalone':[], 'drift':[]}
        self.requests = []
        self.env['_group_catalog'] = self.catalog
        self.message = SimpleNamespace(text='',caption='',document=None,chat=SimpleNamespace(id=7,type='private'),
                                       from_user=SimpleNamespace(id=7,username='owner'))

    def catalog(self, request=None):
        if request is None: return copy.deepcopy(self.state)
        self.requests.append(copy.deepcopy(request))
        if request['revision'] != self.state['revision']:
            raise self.env['GroupMenuError']('Список изменился')
        g = next((g for g in self.state['groups'] if g['id'] == request.get('id')),None)
        action = request['action']
        if action == 'create':
            self.state['groups'].append(dict(id=GROUP_ID, name=request['name'],slot=request['slot'],
                                             enabled=True,domains=sorted(request['domains'])))
        elif action == 'update':
            g.update(name=request['name'],slot=request['slot'],domains=sorted(request['domains']))
        elif action == 'move': g['slot'] = request['slot']
        elif action == 'toggle': g['enabled'] = request['enabled']
        elif action == 'delete': self.state['groups'].remove(g)
        self.state['revision'] = chr(ord(self.state['revision'][0])+1)*64
        return copy.deepcopy(self.state)

    def call(self, data, uid=7):
        call = SimpleNamespace(data=data,id='callback',from_user=SimpleNamespace(id=uid),
                               message=SimpleNamespace(chat=self.message.chat,message_id=10))
        self.env['group_callback'](call)
        return call

    def group(self, domains=None):
        self.state['groups'] = [dict(id=GROUP_ID,name='Ютуб',slot=1,enabled=True,
                                     domains=domains or ['youtube.com','youtu.be'])]
        return self.state['groups'][0]

    def test_group_menu_and_large_group_are_paginated_with_short_callbacks(self):
        self.state['groups'] = [dict(id=f'{n:016x}',name=f'Группа {n}',slot=1,enabled=True,
                                     domains=['x.example']) for n in range(21)]
        text,kb = self.env['_group_index'](self.state,1)
        self.assertIn('21',text)
        self.assertEqual(len([b for row in kb.rows for b in row if b.callback_data.startswith('grp:view:')]),8)
        self.assertTrue(all(b.text.startswith('Вкл ·') for row in kb.rows for b in row if b.callback_data.startswith('grp:view:')))
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for row in kb.rows for b in row))
        paused = self.group()
        paused['enabled'] = False
        _, paused_kb = self.env['_group_index'](self.state)
        self.assertTrue(any(b.text.startswith('Пауза ·') for row in paused_kb.rows for b in row))
        group = self.group([f'{n:03d}.very-long-example-domain.test' for n in range(80)])
        text,kb = self.env['_group_detail'](self.state,group,4)
        self.assertIn('страница 5/8',text)
        self.assertLess(len(text),4096)
        self.assertTrue(all(len(b.callback_data.encode()) <= 64 for row in kb.rows for b in row))

    def test_create_add_remove_rename_move_pause_resume_and_delete(self):
        self.call('grp:new')
        self.message.text='Ютуб'
        self.env['_group_create_name'](self.message)
        self.call('grp:newslot:1')
        self.message.text='youtube.com, www.youtube.com\nyoutu.be'
        self.env['_group_create_domains'](self.message)
        self.assertEqual(self.requests[-1]['domains'],['youtube.com','www.youtube.com','youtu.be'])
        self.assertEqual(len(self.state['groups']),1)
        rev=self.state['revision']
        self.call(f'grp:add:{GROUP_ID}:{rev[:10]}')
        self.message.text='googlevideo.com'
        self.env['_group_edit_text'](self.message,GROUP_ID,rev,'add')
        self.assertIn('googlevideo.com',self.state['groups'][0]['domains'])
        rev=self.state['revision']
        self.call(f'grp:remove:{GROUP_ID}:{rev[:10]}')
        self.message.text='www.youtube.com'
        self.env['_group_edit_text'](self.message,GROUP_ID,rev,'remove')
        self.assertNotIn('www.youtube.com',self.state['groups'][0]['domains'])
        rev=self.state['revision']
        self.call(f'grp:rename:{GROUP_ID}:{rev[:10]}')
        self.message.text='Видео'
        self.env['_group_edit_text'](self.message,GROUP_ID,rev,'rename')
        self.assertEqual(self.state['groups'][0]['name'],'Видео')
        rev=self.state['revision']
        self.call(f'grp:move:{GROUP_ID}:2:{rev[:10]}')
        self.assertEqual(self.state['groups'][0]['slot'],2)
        rev=self.state['revision']
        self.call(f'grp:toggle:{GROUP_ID}:{rev[:10]}')
        self.assertFalse(self.state['groups'][0]['enabled'])
        rev=self.state['revision']
        self.call(f'grp:toggle:{GROUP_ID}:{rev[:10]}')
        self.assertTrue(self.state['groups'][0]['enabled'])
        rev=self.state['revision']
        count=len(self.requests)
        self.call(f'grp:delete:{GROUP_ID}:{rev[:10]}')
        self.assertEqual(len(self.requests),count)
        self.call(f'grp:confirm:{GROUP_ID}:{rev[:10]}')
        self.assertEqual(self.state['groups'],[])
        self.assertEqual(self.requests[-1]['action'],'delete')

    def test_stale_buttons_and_unauthorized_callbacks_cannot_mutate(self):
        self.group()
        self.call(f'grp:move:{GROUP_ID}:2:deadbeef00')
        self.call(f'grp:move:{GROUP_ID}:2:{self.state["revision"][:10]}',uid=42)
        self.assertEqual(self.state['groups'][0]['slot'],1)
        self.assertEqual(self.requests,[])

    def test_text_edit_rejects_stale_revision_and_empty_group(self):
        self.group(['youtube.com'])
        self.message.text='youtube.com'
        self.env['_group_edit_text'](self.message,GROUP_ID,'old'*20,'remove')
        self.assertEqual(self.requests,[])
        self.env['_group_edit_text'](self.message,GROUP_ID,self.state['revision'],'remove')
        self.assertEqual(self.requests,[])
        self.assertEqual(self.state['groups'][0]['domains'],['youtube.com'])

    def test_ipv4_cidr_reaches_catalog_from_bot(self):
        self.call('grp:new')
        self.message.text='Telegram'
        self.env['_group_create_name'](self.message)
        self.call('grp:newslot:1')
        self.message.text='91.108.56.0/22 149.154.160.0/20'
        self.env['_group_create_domains'](self.message)
        self.assertEqual(self.requests[-1]['domains'],['91.108.56.0/22','149.154.160.0/20'])
        self.assertIn('91.108.56.0/22',self.env['_group_domain_key']('91.108.56.0/22'))

    def test_catalog_invocation_uses_structured_stdin_and_does_not_expose_unknown_errors(self):
        fake=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(self.state),''))
        self.env['subprocess']=SimpleNamespace(run=fake,TimeoutExpired=subprocess.TimeoutExpired)
        request={'revision':'a'*64,'action':'create','name':'Ютуб','slot':1,'domains':['youtube.com']}
        self.original_catalog(request)
        self.assertEqual(fake.call_args.args[0],['/opt/bin/python3','/opt/apps/pivas/bin/main/runtime.py','catalog','write'])
        self.assertEqual(json.loads(fake.call_args.kwargs['input']),request)
        fake.return_value=subprocess.CompletedProcess([],1,'','password=SECRET')
        with self.assertRaises(self.env['GroupMenuError']) as exc:self.original_catalog(request)
        self.assertNotIn('SECRET',str(exc.exception))


if __name__ == '__main__': unittest.main()
