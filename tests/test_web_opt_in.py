"""The web server stays off until explicitly enabled, including after boot."""
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "web-src/data/opt/etc/init.d/S99pivas-web"
CLI = ROOT / "work/orig/data/opt/apps/pivas/bin/pivas"


class WebOptInTests(unittest.TestCase):
    def test_boot_does_not_start_with_password_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            web = base / "web"
            web.mkdir()
            (web / "auth").write_text("configured\n")
            script = INIT.read_text().replace("/opt/etc/pivas-web", str(web))
            script = script.replace("/tmp/run", str(base / "run"))
            script = script.replace("/tmp/log", str(base / "log"))
            test_init = base / "S99pivas-web"
            test_init.write_text(script)
            result = subprocess.run(["sh", str(test_init), "start"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("выключен", result.stdout)
            self.assertFalse((base / "run/pivas-web.pid").exists())

            (web / "enabled").touch()
            (web / "auth").unlink()
            result = subprocess.run(["sh", str(test_init), "start"], capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Нет auth-конфига", result.stdout)

    def test_cli_on_and_off_persist_boot_choice(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            web = base / "web"
            web.mkdir()
            (web / "auth").write_text("configured\n")
            log = base / "calls"
            init = base / "S99pivas-web"
            init.write_text("#!/bin/sh\n"
                            f"echo \"$1\" >> '{log}'\n"
                            f"[ \"$1\" != start ] || [ -f '{web / 'enabled'}' ]\n")
            init.chmod(0o755)
            cli = CLI.read_text()
            functions = cli[cli.index("WEB_INIT="):cli.index("# custom2:")]
            functions = functions.replace("/opt/etc/init.d/S99pivas-web", str(init))
            functions = functions.replace("/opt/etc/pivas-web", str(web))
            for action in ("on", "off"):
                result = subprocess.run(["sh", "-c", functions + '\npivas_web_cmd "$@"', "sh", action],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((web / "enabled").exists(), action == "on")
            self.assertEqual(log.read_text().splitlines(), ["start", "stop"])
            result = subprocess.run(["sh", "-c", functions + '\npivas_web_cmd "$@"', "sh", "restart"],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(log.read_text().splitlines(), ["start", "stop"])


if __name__ == "__main__":
    unittest.main()
