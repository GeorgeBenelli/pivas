#!/opt/bin/python3
"""Finish an admin-requested IPK update even when opkg replaces the bot."""
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

STATUS = Path('/opt/tmp/pivas-update-status.json')
LOG = Path('/opt/tmp/pivas-update.log')
LOCK = Path('/opt/tmp/pivas-update.lock')
PAUSED = Path('/opt/etc/pivas.paused')
BOT_INIT = '/opt/etc/init.d/S98telegram4pivas'
WEB_INIT = '/opt/etc/init.d/S99pivas-web'


def _run(argv, timeout):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        with os.fdopen(fd, 'a') as output:
            output.write('\n$ ' + ' '.join(argv[:2]) + '\n')
            output.flush()
            try:
                return subprocess.run(argv, stdout=output, stderr=subprocess.STDOUT,
                                      timeout=timeout, check=False).returncode
            except subprocess.TimeoutExpired:
                output.write('timeout\n')
                return 124
            except OSError as error:
                output.write(type(error).__name__ + '\n')
                return 127
    finally:
        os.chmod(LOG, 0o600)


def perform(package, ipk_path, was_active, web_enabled, run=_run, paused=PAUSED):
    """Install, then restore the services that were enabled before update."""
    errors = []
    installed = run(['opkg', 'install', '--force-reinstall', ipk_path], 240) == 0
    if not installed:
        errors.append('opkg не установил пакет')
    if was_active and package in {'pivas', 'pivas-full', 'xray', 'xray-core'}:
        for _ in range(2):
            if run(['pivas', 'start'], 110) == 0 and not paused.exists():
                break
            time.sleep(2)
        else:
            errors.append('Pivas не запустился после обновления')
    if web_enabled and package in {'pivas-web', 'pivas-full'}:
        if run([WEB_INIT, 'start'], 25) != 0:
            errors.append('веб не запустился')
    if run([BOT_INIT, 'start'], 25) != 0:
        errors.append('бот не запустился')
    return {'ok': installed and not errors, 'installed': installed,
            'pivas_active': was_active and not paused.exists(), 'errors': errors}


def _write_status(package, version, result):
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    temp = STATUS.with_suffix('.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump({'package': package, 'version': version, 'at': int(time.time()),
                   **result}, output, ensure_ascii=False)
    os.replace(temp, STATUS)
    os.chmod(STATUS, 0o600)


def _notify(chat_id, message):
    try:
        spec = importlib.util.spec_from_file_location('telegram_bot_config', '/opt/etc/telegram4pivas/telegram_bot_config.py')
        config = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(config)
        import telebot
        from telebot import apihelper
        proxy = getattr(config, 'proxy_url', 'http://127.0.0.1:1098')
        if proxy:
            apihelper.proxy = {'http': proxy, 'https': proxy}
        telebot.TeleBot(config.token).send_message(chat_id, message)
    except Exception:
        # The status file remains available after /start if Telegram is down.
        pass


def main(argv):
    if len(argv) != 6:
        return 2
    ipk_path, package, version, chat_id, active, web = argv
    time.sleep(3)  # Let the bot's acceptance message reach Telegram first.
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    with LOCK.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _notify(int(chat_id), 'Другое обновление Pivas уже выполняется.')
            return 1
        try:
            try:
                result = perform(package, ipk_path, active == '1', web == '1')
            except Exception as error:
                result = {'ok': False, 'installed': False, 'pivas_active': False,
                          'errors': ['ошибка обновления: ' + type(error).__name__]}
            _write_status(package, version, result)
            if result['ok']:
                message = 'Обновление {} {} установлено. Pivas: {}.'.format(
                    package, version, 'работает' if result['pivas_active'] else 'прежняя пауза сохранена')
            else:
                message = 'Обновление {} {} завершилось с ошибкой: {}. Подробности: /opt/tmp/pivas-update.log'.format(
                    package, version, '; '.join(result['errors']))
            _notify(int(chat_id), message)
            return 0 if result['ok'] else 1
        finally:
            try: os.unlink(ipk_path)
            except OSError: pass
            try: os.unlink(__file__)
            except OSError: pass


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
