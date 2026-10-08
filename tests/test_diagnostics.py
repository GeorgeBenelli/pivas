import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_runtime
import diagnostics

class DiagnosticsTests(unittest.TestCase):
    def test_redacts_common_secret_formats(self):
        values=['token = FAKE_SECRET', '{"token": "FAKE_SECRET"}', "password='FAKE_SECRET'", 'https://api.telegram.org/bot123456:FAKE_SECRET/getMe','https://api.telegram.org/file/bot123456:FAKE_SECRET/x','https://user:FAKE_SECRET@example.com', 'vless://FAKE_SECRET@host', '"privateKey": "FAKE_SECRET"']
        for value in values:
            with self.subTest(value=value):self.assertNotIn('FAKE_SECRET',diagnostics.redact(value))
        self.assertNotIn('123e4567-e89b-12d3-a456-426614174000',diagnostics.redact('123e4567-e89b-12d3-a456-426614174000'))
    def test_report_is_bounded_and_uses_uncached_dns_and_explicit_proxy(self):
        calls=[]
        def run(args,**kwargs):
            calls.append((args,kwargs));return subprocess.CompletedProcess(args,0,'status: NXDOMAIN\n','')
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostics,'BASE',Path(tmp)),patch.object(diagnostics.subprocess,'run',run):
            report=diagnostics.build_report()
        self.assertIn('NXDOMAIN',report)
        dns=[a for a,k in calls if a[0]=='dig']
        self.assertEqual(len(dns),5)
        self.assertNotEqual(dns[-1][-2],dns[-2][-2])
        self.assertTrue(all('+comments' in a for a in dns))
        curls=[a for a,k in calls if a[0]=='curl']
        self.assertEqual(curls[-1][curls[-1].index('--noproxy')+1],'*')
        self.assertEqual(curls[0][curls[0].index('--noproxy')+1],'')
        self.assertTrue(all(k['timeout']<=6 for a,k in calls))
        self.assertFalse(any('setpass' in str(a) or 'restart' in a for a,k in calls))
    def test_exhausted_budget_does_not_start_commands(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostics,'BASE',Path(tmp)),patch.object(diagnostics.subprocess,'run') as run:
            result=diagnostics.build_report(budget=0)
        run.assert_not_called();self.assertIn('лимит времени',result)
    def test_tail_and_final_redaction(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(diagnostics,'BASE',Path(tmp)):
            p=Path(tmp)/'log';p.write_text('first\nsecond\npassword=FAKE_SECRET\n')
            self.assertEqual(diagnostics.tail('/log',2),'second\npassword=FAKE_SECRET')
            self.assertNotIn('FAKE_SECRET',diagnostics.redact(diagnostics.tail('/log',2)))

if __name__=='__main__':unittest.main()
