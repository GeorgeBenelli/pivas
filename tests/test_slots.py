import json
import unittest
from unittest.mock import patch
import test_runtime as fixture
import slots
from test_swap_slots import profile

rt = fixture.rt


class SlotNamesTests(unittest.TestCase):
    put = fixture.RuntimeTests.put
    setUp = fixture.RuntimeTests.setUp
    tearDown = fixture.RuntimeTests.tearDown

    def rename(self, slot, name, revision=None):
        return slots.handle(rt, {'action':'rename', 'slot':slot, 'name':name,
                                 'revision':revision or slots.view(rt)['revision']})

    def test_default_read_creates_no_file_and_calls_no_service(self):
        self.assertEqual(slots.view(rt)['names'], {'1':'Слот 1','2':'Слот 2'})
        self.assertFalse(rt.path(slots.CONFIG).exists())
        self.assertEqual(self.router.restarts(), [])

    def test_unicode_rename_persists_and_does_not_restart_or_change_connections(self):
        self.put(rt.XRAY,json.dumps({'outbounds':[profile(1),profile(2)]}))
        before=rt.path(rt.XRAY).read_bytes()
        result=self.rename(1,'  Германия 🇩🇪  ')
        self.assertEqual(result['names'], {'1':'Германия 🇩🇪','2':'Слот 2'})
        self.assertEqual(slots.load(rt),result['names'])
        self.assertEqual(rt.path(slots.CONFIG).stat().st_mode & 0o777,0o600)
        self.assertEqual(rt.path(rt.XRAY).read_bytes(),before)
        self.assertEqual(self.router.restarts(),[])

    def test_stale_write_does_not_overwrite_either_name(self):
        rev=slots.view(rt)['revision'];self.rename(1,'Основной')
        with self.assertRaises(ValueError):self.rename(2,'Другой',rev)
        self.assertEqual(slots.load(rt)['2'],'Слот 2')

    def test_bad_values_do_not_create_configuration(self):
        for value in ('',' ','x'*33,'name\nline','name\0','hidden\u202e',123,False):
            with self.subTest(value=value), self.assertRaises(ValueError):self.rename(1,value)
        self.assertFalse(rt.path(slots.CONFIG).exists())
        for slot in (True,'1',0,3,None):
            with self.assertRaises(ValueError):self.rename(slot,'test')

    def test_atomic_failure_keeps_previous_name(self):
        self.rename(1,'Первый');before=rt.path(slots.CONFIG).read_bytes()
        with patch.object(rt,'atomic',side_effect=OSError('disk full')), self.assertRaises(OSError):self.rename(2,'Второй')
        self.assertEqual(rt.path(slots.CONFIG).read_bytes(),before)

    def test_cli_reset_and_idempotent_write(self):
        slots.cli(rt,['name','2','Резервный'])
        with patch.object(rt,'atomic') as atomic:self.rename(2,'Резервный')
        atomic.assert_not_called()
        slots.cli(rt,['reset','2'])
        self.assertEqual(slots.load(rt),slots.DEFAULTS)
        with self.assertRaises(ValueError):slots.cli(rt,['name','2'])

    def test_swap_preserves_numbered_names(self):
        self.put(rt.XRAY,json.dumps({'outbounds':[profile(1),profile(2)],'inbounds':[],'routing':{'rules':[]}}))
        self.rename(1,'Германия');self.rename(2,'Финляндия')
        before=rt.path(slots.CONFIG).read_bytes()
        rt.swap_slots()
        self.assertEqual(rt.path(slots.CONFIG).read_bytes(),before)

    def test_corrupt_file_is_reported_without_overwriting_it(self):
        self.put(slots.CONFIG,'{"version":99}')
        with self.assertRaises(ValueError):slots.view(rt)
        self.assertEqual(rt.path(slots.CONFIG).read_text(),'{"version":99}')


if __name__=='__main__':unittest.main()
