"""Missing dependency scripts after legacy removal must recover without opkg."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import test_runtime
import test_web_api
import dns_services as ds
import diagnostics

ROOT = test_runtime.ROOT


class DnsServicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        for package, binary, script in ds.SERVICES:
            self.put(binary, '#!/bin/sh\nexit 0\n', 0o755)
        self.put(ds.RC, '# test rc.func\n', 0o644)
        templates = self.base / ds.TEMPLATES.lstrip('/')
        shutil.copytree(ROOT/'work/orig/data'/ds.TEMPLATES.lstrip('/'), templates)

    def tearDown(self):
        self.tmp.cleanup()

    def path(self, name):
        return self.base / name.lstrip('/')

    def put(self, name, text, mode=0o644):
        target = self.path(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        target.chmod(mode)
        return target

    def test_missing_scripts_restore_offline_without_touching_configs_or_archives(self):
        retained = []
        for name in ('/opt/etc/dnsmasq.conf', '/opt/etc/dnscrypt-proxy.toml',
                     '/opt/etc/dnscrypt-proxy-slot2.toml', '/opt/etc/pivas.paused',
                     '/opt/etc/init.d/K56dnsmasq', '/opt/etc/init.d/K09dnscrypt-proxy2'):
            retained.append(self.put(name, 'preserved:'+name))
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in retained}
        with patch('subprocess.run') as run, patch('subprocess.Popen') as spawn:
            restored = ds.ensure(self.base)
        run.assert_not_called(); spawn.assert_not_called()
        self.assertEqual(set(restored), {s for _, _, s in ds.SERVICES})
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in retained})
        self.assertTrue(all(item['state'] == 'ok' for item in ds.status(self.base)))
        for _, _, script in ds.SERVICES:
            self.assertEqual(self.path(script).stat().st_mode & 0o777, 0o755)
        self.assertEqual(list(self.path('/opt/etc/init.d').glob('.pivas-dns-*')), [])

    def test_healthy_custom_scripts_are_preserved_and_repair_is_idempotent(self):
        for _, _, script in ds.SERVICES:
            self.put(script, '#!/bin/sh\n# custom\nENABLED=no\n', 0o750)
        before = {s: (self.path(s).read_bytes(), self.path(s).stat().st_mtime_ns,
                      self.path(s).stat().st_mode) for _, _, s in ds.SERVICES}
        self.assertEqual(ds.ensure(self.base), [])
        self.assertEqual(ds.ensure(self.base), [])
        self.assertEqual(before, {s: (self.path(s).read_bytes(), self.path(s).stat().st_mtime_ns,
                                     self.path(s).stat().st_mode) for _, _, s in ds.SERVICES})

    def test_nonexecutable_script_regains_permissions_without_changing_contents(self):
        ds.ensure(self.base)
        script = self.path(ds.SERVICES[0][2]); script.chmod(0o640)
        before = script.read_bytes()
        self.assertEqual(ds.ensure(self.base), [ds.SERVICES[0][2]])
        self.assertEqual(script.read_bytes(), before)
        self.assertTrue(os.access(script, os.X_OK))

    def test_missing_binaries_report_both_packages_before_any_repair(self):
        for _, binary, _ in ds.SERVICES:
            self.path(binary).unlink()
        with self.assertRaisesRegex(RuntimeError, 'dnsmasq-full.*dnscrypt-proxy2'):
            ds.ensure(self.base)
        self.assertFalse(any(self.path(s).exists() for _, _, s in ds.SERVICES))

    def test_missing_rc_or_template_prevents_partial_repair(self):
        for name in (ds.RC, ds.TEMPLATES + '/S09dnscrypt-proxy2'):
            target = self.path(name); before = target.read_bytes(); target.unlink()
            with self.subTest(name=name), self.assertRaises(RuntimeError):
                ds.ensure(self.base)
            self.assertFalse(any(self.path(s).exists() for _, _, s in ds.SERVICES))
            target.write_bytes(before)

    def test_corrupt_existing_script_and_broken_symlink_are_not_overwritten(self):
        target = self.put(ds.SERVICES[0][2], 'broken custom script', 0o755)
        with self.assertRaisesRegex(RuntimeError, 'dnsmasq-full'):
            ds.ensure(self.base)
        self.assertEqual(target.read_text(), 'broken custom script')
        target.unlink(); target.symlink_to(self.base/'missing-target')
        with self.assertRaises(RuntimeError): ds.ensure(self.base)
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.base/'missing-target').exists())

    def test_concurrent_creation_is_not_overwritten(self):
        original = os.link
        def concurrent(src, dest):
            if not Path(dest).exists():
                Path(dest).write_text('#!/bin/sh\n# concurrent install\n')
                Path(dest).chmod(0o755)
            return original(src, dest)
        with patch.object(ds.os, 'link', side_effect=concurrent): ds.ensure(self.base)
        for _, _, script in ds.SERVICES:
            self.assertIn('concurrent install', self.path(script).read_text())

    def test_read_only_storage_produces_actionable_error(self):
        with patch.object(ds.os, 'link', side_effect=OSError('PRIVATE_OUTPUT')):
            with self.assertRaisesRegex(RuntimeError, 'доступность /opt') as failure:
                ds.ensure(self.base)
        self.assertNotIn('PRIVATE_OUTPUT', str(failure.exception))
        self.assertEqual(list(self.path('/opt/etc/init.d').glob('.pivas-dns-*')), [])

    def test_diagnostic_inventory_never_repairs(self):
        with patch.object(diagnostics, 'BASE', self.base):
            report = diagnostics.build_report(budget=0)
        self.assertIn('Файлы DNS-служб', report)
        self.assertIn('S09dnscrypt-proxy2: отсутствует', report)
        self.assertFalse(any(self.path(s).exists() for _, _, s in ds.SERVICES))

    def test_restored_scripts_pass_correct_service_and_config_to_rc(self):
        ds.ensure(self.base)
        self.put(ds.RC, 'printf "%s|%s|%s\\n" "$PROCS" "$ARGS" "$1"\n')
        for _, _, script in ds.SERVICES:
            # Redirect only rc.func on this host; execute the real shell template.
            source = self.path(script).read_text().replace(ds.RC, str(self.path(ds.RC)))
            result = subprocess.run(['sh', '-c', source, script, 'status'], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = 'dnsmasq||status' if script.endswith('dnsmasq') else 'dnscrypt-proxy|-config /opt/etc/dnscrypt-proxy.toml|status'
            self.assertEqual(result.stdout.strip(), expected)

    def test_legacy_removal_stops_services_without_renaming_dependency_files(self):
        ds.ensure(self.base)
        source = (ROOT/'work/orig/data/opt/apps/pivas/bin/main/setup').read_text()
        functions = '\n'.join(re.search(r'^'+name+r'\(\)\{.*?^\}', source, re.M|re.S)[0]
                              for name in ('stop_dnsmasq', 'stop_crypt'))
        for _, _, script in ds.SERVICES:
            self.put(script, '#!/bin/sh\necho alive\n', 0o755)
            functions = functions.replace(script, str(self.path(script)))
        shell = 'ready() { :; }; when_ok() { :; }; when_bad() { :; }; service_action() { echo "$@"; };\n' + functions + '\nstop_dnsmasq\nstop_crypt\n'
        result = subprocess.run(['bash', '-c', shell], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(self.path(s).exists() for _, _, s in ds.SERVICES))

    def test_runtime_entry_repairs_under_shared_lock(self):
        with patch.object(test_runtime.rt, 'BASE', self.base):
            self.assertEqual(test_runtime.rt.main(['dns-services']), 0)
        self.assertTrue(all(item['state'] == 'ok' for item in ds.status(self.base)))

    def test_start_refuses_missing_binary_before_setting_pause(self):
        import sys
        source = (ROOT/'work/orig/data/opt/apps/pivas/bin/pivas').read_text()
        # Execute the actual startup preflight and first mutation, with a local
        # runtime wrapper; no router command is permitted in this fixture.
        prefix = source.split('start | on | resume)\n', 1)[1].split('        touch "$PIVAS_PAUSE_FLAG"', 1)[0]
        runtime = self.base/'runtime-check.py'
        runtime.write_text('import sys\nfrom pathlib import Path\nsys.path.insert(0, '+repr(str(test_runtime.MODULE.parent))+')\nimport dns_services\ndns_services.ensure(Path('+repr(str(self.base))+'))\n')
        prefix = prefix.replace('/opt/bin/python3', sys.executable).replace('/opt/apps/pivas/bin/main/runtime.py', str(runtime))
        pause = self.base/'pause'
        script = prefix + '\ntouch "'+str(pause)+'"\n'
        self.path(ds.SERVICES[0][1]).unlink()
        result = subprocess.run(['sh', '-c', script], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(pause.exists())
        self.assertIn('dnsmasq-full', result.stderr)
        self.put(ds.SERVICES[0][1], '#!/bin/sh\nexit 0\n', 0o755)
        result = subprocess.run(['sh', '-c', script], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(pause.exists())

    def test_web_surfaces_only_dns_preflight_error_on_start(self):
        message = 'Pivas: DNS-службы: /opt/sbin/dnsmasq: отсутствует; восстановление: opkg install --force-reinstall dnsmasq-full'
        for action in ('start', 'set', 'stop'):
            req = test_web_api.Request('/api/control', dict(action=action, slot=1, url='vless://PRIVATE_URL'))
            result = subprocess.CompletedProcess([], 1, 'PRIVATE_STDOUT', 'PRIVATE_STDERR\n'+message+'\n')
            with patch.object(test_web_api.web.subprocess, 'run', return_value=result): req._api()
            self.assertEqual(req.status, 409)
            self.assertNotIn('PRIVATE_', str(req.data()))
            self.assertEqual(req.data()['error'] == message, action == 'start')


if __name__ == '__main__':
    unittest.main()
