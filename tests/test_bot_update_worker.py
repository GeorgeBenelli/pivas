"""A Telegram package update must restore the pre-update service state."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / 'bot/update_worker.py'
spec = importlib.util.spec_from_file_location('pivas_update_worker', SOURCE)
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class UpdateWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.paused = Path(self.tmp.name) / 'paused'
        self.calls = []
    def tearDown(self):
        self.tmp.cleanup()
    def run_command(self, argv, timeout):
        self.calls.append((argv, timeout))
        if argv == ['pivas', 'start']:
            self.paused.unlink(missing_ok=True)
        return 0
    def test_full_update_restores_pivas_web_and_bot(self):
        self.paused.touch()
        result = worker.perform('pivas-full', '/opt/tmp/update.ipk', True, True,
                                run=self.run_command, paused=self.paused)
        self.assertTrue(result['ok'])
        self.assertEqual([c[0] for c in self.calls],
                         [['opkg','install','--force-reinstall','/opt/tmp/update.ipk'],
                          ['pivas','start'],[worker.WEB_INIT,'start'],[worker.BOT_INIT,'start']])
    def test_existing_pause_is_preserved(self):
        self.paused.touch()
        result = worker.perform('pivas', '/opt/tmp/update.ipk', False, False,
                                run=self.run_command, paused=self.paused)
        self.assertTrue(result['ok'])
        self.assertTrue(self.paused.exists())
        self.assertNotIn(['pivas','start'], [c[0] for c in self.calls])
    def test_failed_first_start_is_retried(self):
        self.paused.touch()
        attempts = 0
        def run(argv, timeout):
            nonlocal attempts
            if argv == ['pivas','start']:
                attempts += 1
                if attempts == 1:return 1
            return self.run_command(argv, timeout)
        with patch.object(worker.time, 'sleep'):
            result = worker.perform('pivas', '/opt/tmp/update.ipk', True, False,
                                    run=run, paused=self.paused)
        self.assertTrue(result['ok'])
        self.assertEqual(attempts, 2)
    def test_failed_install_still_attempts_to_restore_connectivity(self):
        self.paused.touch()
        def run(argv, timeout):
            if argv[0] == 'opkg':return 1
            return self.run_command(argv, timeout)
        result = worker.perform('pivas-full', '/opt/tmp/update.ipk', True, True,
                                run=run, paused=self.paused)
        self.assertFalse(result['ok'])
        self.assertIn(['pivas','start'], [c[0] for c in self.calls])
        self.assertIn([worker.BOT_INIT,'start'], [c[0] for c in self.calls])
    def test_bot_update_restarts_bot_without_changing_pivas(self):
        result = worker.perform('telegram4pivas', '/opt/tmp/bot.ipk', True, False,
                                run=self.run_command, paused=self.paused)
        self.assertTrue(result['ok'])
        self.assertEqual([c[0] for c in self.calls][-1], [worker.BOT_INIT,'start'])
        self.assertNotIn(['pivas','start'], [c[0] for c in self.calls])


if __name__ == '__main__':unittest.main()
