"""The install guard must reject foreign file owners without hardcoded product names."""
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('installer_builder', ROOT / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class InstallerGuardTests(unittest.TestCase):
    def test_repeated_install_checks_dns_even_when_opkg_skips_postinst(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'mipsel').mkdir()
            package = root/'dummy.ipk'; package.write_bytes(b'dummy')
            old_dist = builder.DIST
            try:
                builder.DIST = root
                builder.installer('mipsel-3.4', [package], '')
            finally:
                builder.DIST = old_dist
            installer = root/'mipsel/install-pivas.sh'
            installer.write_bytes(installer.read_bytes().replace(b'/opt/tmp', str(root/'opt-tmp').encode()))
            bins = root/'bin'; bins.mkdir()
            for name, content in {
                'id': '#!/bin/sh\necho 0\n',
                'opkg': '#!/bin/sh\n[ "$1" != print-architecture ] || echo "arch mipsel-3.4 10"\nexit 0\n',
                'pivas': '#!/bin/sh\n[ "$1" = repair-dns ] || exit 90\nprintf "%s\\n" "$1" >> "$DNS_TEST_LOG"\nexit "$DNS_TEST_RC"\n',
            }.items():
                path = bins/name; path.write_text(content); path.chmod(0o755)
            env = {k: v for k, v in os.environ.items() if k not in ('PIVAS_URL1', 'PIVAS_URL2', 'BOT_TOKEN', 'BOT_CHATID')}
            env.update(PATH=str(bins)+':'+env['PATH'], DNS_TEST_LOG=str(root/'calls'))
            for code in (0, 14):
                env['DNS_TEST_RC'] = str(code)
                result = subprocess.run(['sh', str(installer)], input='', capture_output=True, text=True, env=env)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertEqual('Пакеты установлены.' in result.stdout, code == 0)
            self.assertEqual((root/'calls').read_text().splitlines(), ['repair-dns', 'repair-dns'])

    def test_interactive_setup_skips_without_tty_and_keeps_env_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dist = builder.DIST
            try:
                builder.DIST = Path(tmp)
                (Path(tmp) / 'mipsel').mkdir()
                package = Path(tmp) / 'dummy.ipk'
                package.write_bytes(b'dummy')
                builder.installer('mipsel-3.4', [package], '')
                header = (Path(tmp) / 'mipsel/install-pivas.sh').read_bytes().split(b'\n__PAYLOAD__\n')[0].decode()
            finally:
                builder.DIST = old_dist
            setup = header[header.index('# Interactive setup'):header.index("echo 'Пакеты установлены")]
            shell = "set -eu\nTTY_STATE=\npivas() { printf '%s|%s|%s\\n' \"$1\" \"$2\" \"${3:-}\"; }\n" + setup
            result = subprocess.run(['sh', '-c', shell], input='', text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(result.stderr, '')
            env = os.environ.copy()
            env.update(PIVAS_URL1='vless://first?x=1&y=2', PIVAS_URL2='vless://second',
                       BOT_TOKEN='123:abc', BOT_CHATID='456')
            result = subprocess.run(['sh', '-c', shell], input='', text=True,
                                    capture_output=True, env=env)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [
                'vless|set-1|vless://first?x=1&y=2', 'vless|set-2|vless://second',
                'bot|token|123:abc', 'bot|id|456'])

    def test_clean_package_creates_parents_and_executable_scripts(self):
        data = {'opt/apps/pivas/bin/pivas': (b'#!/bin/sh\n', 0o755)}
        ctl = {'control': (b'Package: pivas\nVersion: test\n', 0o644),
               'postinst': (b'#!/bin/sh\nexit 0\n', 0o644)}
        with tempfile.TemporaryDirectory() as tmp:
            old_dist = builder.DIST
            try:
                builder.DIST = Path(tmp)
                package, _, _ = builder.package('pivas', 'all', data, ctl)
                with tarfile.open(package) as outer:
                    for member, expected in [('data.tar.gz', './opt/apps/pivas/bin'),
                                             ('control.tar.gz', './postinst')]:
                        payload = outer.extractfile('./' + member).read()
                        with tarfile.open(fileobj=io.BytesIO(payload)) as inner:
                            entry = inner.getmember(expected)
                            if member == 'data.tar.gz':
                                self.assertTrue(entry.isdir())
                            else:
                                self.assertEqual(entry.mode & 0o111, 0o111)
            finally:
                builder.DIST = old_dist

    def run_guard(self, receipts, alternate=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, files in receipts.items():
                directory = root / ('var/lib/opkg/info' if alternate else 'lib/opkg/info')
                directory.mkdir(parents=True, exist_ok=True)
                (directory / (name + '.list')).write_text('\n'.join(files) + '\n')
            guard = builder.INSTALL_GUARD.replace('/opt/lib/opkg/info/', str(root / 'lib/opkg/info') + '/').replace('/opt/var/lib/opkg/info/', str(root / 'var/lib/opkg/info') + '/')
            return subprocess.run(['sh', '-c', guard], text=True, capture_output=True)

    def test_clean_install_and_unrelated_files_allowed(self):
        self.assertEqual(self.run_guard({}).returncode, 0)
        self.assertEqual(self.run_guard({'other': ['/opt/bin/other']}).returncode, 0)

    def test_existing_pivas_allowed(self):
        for name in ('pivas', 'pivas-full'):
            self.assertEqual(self.run_guard({name: ['/opt/etc/ndm/netfilter.d/100-dns-local']}).returncode, 0)

    def test_foreign_owner_rejected_in_both_database_locations(self):
        for alternate in (False, True):
            result = self.run_guard({'previous-routing': ['/opt/etc/ndm/netfilter.d/100-dns-local']}, alternate)
            self.assertEqual(result.returncode, 1)
            self.assertIn('previous-routing', result.stderr)


if __name__ == '__main__':
    unittest.main()
