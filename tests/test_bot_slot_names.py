"""Actual Telegram handlers with mock delivery; no external messages."""
import ast
import copy
import json
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import test_bot_groups as group_tests
import test_runtime
import slots


class SlotBotTests(unittest.TestCase):
    catalog = group_tests.GroupBotTests.catalog
    def setUp(self):
        group_tests.GroupBotTests.setUp(self)
        selected={'_slot_state','_slot_labels','_slot_label','_slot_show','slot_names_menu',
                  'slot_name_callback','_slot_name_text'}
        tree=ast.parse(group_tests.SOURCE.read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in selected]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(group_tests.SOURCE),'exec'),self.env)
        self.actual_state=self.env['_slot_state']
        self.names={'names':{'1':'Германия','2':'Финляндия'},'revision':'a'*64}
        self.requests=[]
        def state(request=None):
            if request:
                if request['revision']!=self.names['revision']:raise self.env['GroupMenuError']('Названия слотов изменились')
                try:value=slots.DEFAULTS[str(request['slot'])] if request['action']=='reset' else slots.name(request['name'])
                except ValueError as e:raise self.env['GroupMenuError'](str(e))
                self.requests.append(request)
                self.names['names'][str(request['slot'])]=value
                self.names['revision']='b'*64
            return copy.deepcopy(self.names)
        self.env['_slot_state']=state
        self.env['_connections_keyboard']=lambda:'connections'
        self.env['_cancel_kb']=lambda:'cancel'
        self.env['_is_cancel']=lambda text:text=='Отмена'
        self.call=SimpleNamespace(id='call',data='sname:rename:2:'+'a'*10,
                                  message=self.message,from_user=self.message.from_user)

    def test_menu_and_group_routes_show_shared_names_with_numeric_callbacks(self):
        self.env['slot_names_menu'](self.message)
        rows=self.bot.send_message.call_args.kwargs['reply_markup'].rows
        self.assertEqual(rows[0][0].text,'✎ 1 · Германия')
        self.assertEqual(rows[1][0].callback_data,'sname:rename:2:'+'a'*10)
        group=dict(id='0123456789abcdef',name='YouTube',slot=1,enabled=True,domains=['youtube.com'])
        text,kb=self.env['_group_detail'](self.state,group)
        self.assertIn('Германия',text)
        move=next(b for row in kb.rows for b in row if b.callback_data.startswith('grp:move'))
        self.assertIn('Финляндия',move.text)
        self.assertIn(':2:',move.callback_data)

    def test_rename_flow_keeps_slot_number_and_has_no_vless_command(self):
        self.env['slot_name_callback'](self.call)
        args=self.bot.register_next_step_handler.call_args.args
        self.assertEqual(args[2:],(2,'a'*64,7))
        self.message.text='Резерв 🇫🇮'
        args[1](self.message,*args[2:])
        self.assertEqual(self.names['names'],{'1':'Германия','2':'Резерв 🇫🇮'})
        self.assertEqual(self.requests[0]['slot'],2)
        self.assertEqual(self.requests[0]['action'],'rename')

    def test_cancel_reset_and_invalid_name_retry(self):
        self.message.text='Отмена'
        self.env['_slot_name_text'](self.message,1,'a'*64,7)
        self.assertEqual(self.requests,[])
        self.message.text='x'*33
        self.env['_slot_name_text'](self.message,1,'a'*64,7)
        self.assertEqual(self.requests,[])
        self.bot.register_next_step_handler.assert_called()
        self.message.text='/default'
        self.env['_slot_name_text'](self.message,1,'a'*64,7)
        self.assertEqual(self.names['names']['1'],'Слот 1')

    def test_unauthorized_callback_and_text_never_write(self):
        self.call.from_user=SimpleNamespace(id=99)
        self.env['slot_name_callback'](self.call)
        self.bot.answer_callback_query.assert_called_with('call','Доступ запрещён',show_alert=True)
        self.message.from_user=SimpleNamespace(id=99)
        self.env['_slot_name_text'](self.message,2,'a'*64,7)
        self.assertEqual(self.requests,[])
        self.bot.register_next_step_handler.assert_not_called()

    def test_stale_menu_is_rejected_before_prompt(self):
        self.call.data='sname:rename:1:'+'z'*10
        self.env['slot_name_callback'](self.call)
        self.bot.register_next_step_handler.assert_not_called()
        self.assertEqual(self.requests,[])

    def test_runtime_write_uses_stdin_without_shell_and_redacts_unknown_errors(self):
        run=Mock(return_value=subprocess.CompletedProcess([],0,json.dumps(self.names),''))
        self.env['subprocess']=SimpleNamespace(run=run,TimeoutExpired=subprocess.TimeoutExpired)
        payload={'action':'rename','slot':1,'name':'$(touch /tmp/no)','revision':'a'*64}
        self.assertEqual(self.actual_state(payload),self.names)
        self.assertEqual(run.call_args.args[0][-2:],['slots','write'])
        self.assertEqual(json.loads(run.call_args.kwargs['input']),payload)
        self.assertNotIn('shell',run.call_args.kwargs)
        run.return_value=subprocess.CompletedProcess([],1,'','FAKE_SECRET')
        with self.assertRaises(self.env['GroupMenuError']) as caught:self.actual_state(payload)
        self.assertNotIn('FAKE_SECRET',str(caught.exception))


if __name__=='__main__':unittest.main()
