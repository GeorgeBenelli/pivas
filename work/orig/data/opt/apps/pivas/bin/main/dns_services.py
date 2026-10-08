"""Check DNS dependencies and restore only their missing init scripts.

No opkg calls: this also runs inside opkg postinst. Never replace DNS configs,
existing custom scripts, or archived K* scripts, and never start a service.
"""
import os
from pathlib import Path
import stat
import tempfile

SERVICES = (
    ('dnsmasq-full', '/opt/sbin/dnsmasq', '/opt/etc/init.d/S56dnsmasq'),
    ('dnscrypt-proxy2', '/opt/sbin/dnscrypt-proxy', '/opt/etc/init.d/S09dnscrypt-proxy2'),
)
RC = '/opt/etc/init.d/rc.func'
TEMPLATES = '/opt/apps/pivas/etc/service-templates'
ERROR_PREFIX = 'DNS-службы: '


def status(base=Path('/')):
    """Read-only inventory shared with diagnostics."""
    result = []
    for package, binary, script in SERVICES:
        for name in (binary, script):
            target = base / name.lstrip('/')
            state = 'ok'
            if not target.is_file():
                state = 'отсутствует или не является обычным файлом'
            elif not target.stat().st_size:
                state = 'пустой файл'
            elif not os.access(target, os.X_OK):
                state = 'нет права исполнения'
            elif name == script:
                try:
                    with target.open('rb') as handle:
                        if handle.read(2) != b'#!':
                            state = 'повреждён скрипт (нет shebang)'
                except OSError:
                    state = 'нет доступа для чтения'
            result.append(dict(package=package, path=name, state=state))
    target = base / RC.lstrip('/')
    result.append(dict(package='Entware', path=RC, state='ok' if target.is_file() and
                      target.stat().st_size and os.access(target, os.R_OK) else 'отсутствует, пуст или недоступен'))
    return result


def ensure(base=Path('/')):
    base = Path(base)
    inventory = status(base)
    issues, plans = [], []
    scripts = {entry[2] for entry in SERVICES}
    for entry in inventory:
        if entry['state'] == 'ok':
            continue
        name, package = entry['path'], entry['package']
        target = base / name.lstrip('/')
        if name in scripts and not os.path.lexists(target):
            template = base / TEMPLATES.lstrip('/') / target.name
            try:
                data = template.read_bytes()
                if not data.startswith(b'#!/bin/sh\n'):
                    raise ValueError()
            except (OSError, ValueError):
                issues.append('нет исправного шаблона ' + target.name + '; переустановите пакет Pivas')
            else:
                plans.append(('create', target, data))
        elif (name in scripts and entry['state'] == 'нет права исполнения' and
              not target.is_symlink() and target.read_bytes().startswith(b'#!')):
            plans.append(('chmod', target, None))
        else:
            issues.append(name + ': ' + entry['state'] +
                          ('; восстановите rc.func из установщика Entware для этого роутера' if name == RC else
                           '; восстановление: opkg install --force-reinstall ' + package))
    # Find every missing dependency before touching even one init script.
    if issues:
        raise RuntimeError(ERROR_PREFIX + ' | '.join(issues))
    repaired = []
    for action, target, data in plans:
        try:
            if action == 'chmod':
                target.chmod(stat.S_IMODE(target.stat().st_mode) | 0o111)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.pivas-dns-', delete=False) as handle:
                    temporary = Path(handle.name)
                    try:
                        handle.write(data)
                        handle.flush()
                        os.fchmod(handle.fileno(), 0o755)
                        os.fsync(handle.fileno())
                        # Publish atomically, without replacing a concurrent install.
                        os.link(temporary, target)
                    except FileExistsError:
                        pass
                    finally:
                        temporary.unlink()
            repaired.append('/' + target.relative_to(base).as_posix())
        except OSError:
            raise RuntimeError(ERROR_PREFIX + 'не удалось восстановить /' + target.relative_to(base).as_posix() +
                               '; проверьте доступность /opt для записи и свободное место') from None
    issues = [entry['path'] + ': ' + entry['state'] for entry in status(base) if entry['state'] != 'ok']
    if issues:
        raise RuntimeError(ERROR_PREFIX + ' | '.join(issues))
    return repaired
