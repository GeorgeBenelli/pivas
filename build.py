#!/usr/bin/env python3
"""One source-to-release pipeline. No package/installer ever takes stale dist input."""
import argparse
import ast
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
VERSIONS = {"pivas": "1.1.9_beta-10-25-custom25", "pivas-web": "1.0-custom20", "telegram4pivas": "1.2-custom14", "pivas-full": "1.1.9-25-custom31", "xray": "26.3.27-1-custom4", "xray-core": "26.3.27-2", "pivas-quic-probe": "0.1-1"}
EPOCH = 1790370000

# Refuse an unrelated owner of shared network hooks, independently of its name.
INSTALL_GUARD = '''for receipt in /opt/lib/opkg/info/*.list /opt/var/lib/opkg/info/*.list; do
    [ -f "$receipt" ] || continue
    owner=${receipt##*/}; owner=${owner%.list}
    case "$owner" in pivas|pivas-full) continue ;; esac
    if grep -qE '^/?opt/etc/(ndm/netfilter.d/100-dns-local|init.d/S95pivas-routes)$' "$receipt"; then
        echo "Установка остановлена: сетевые файлы уже принадлежат пакету $owner. Сначала выполните миграцию." >&2
        exit 1
    fi
done'''


def archive(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w", format=tarfile.USTAR_FORMAT) as t:
        # opkg does not create missing parent directories for file-only archives.
        directories = sorted({
            parent.as_posix()
            for name in files
            for parent in Path(name).parents
            if parent.as_posix() != "."
        }, key=lambda value: (value.count("/"), value))
        for name in directories:
            info = tarfile.TarInfo("./" + name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = EPOCH
            t.addfile(info)
        for name, (data, mode) in sorted(files.items()):
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            info.mode = mode
            info.uid = info.gid = 0
            info.uname = info.gname = "root"
            info.mtime = EPOCH
            t.addfile(info, io.BytesIO(data))
    return gzip.compress(raw.getvalue(), mtime=EPOCH)


def tree(path):
    files = {}
    for p in path.rglob("*"):
        if not p.is_file() or any(x.startswith(".") or x == "__pycache__" for x in p.relative_to(path).parts) or p.suffix in (".pyc", ".log") or p.name == "telegram_bot_config.py":
            continue
        data = p.read_bytes()
        mode = 0o755 if data.startswith(b"#!") or p.parent.name == "libs" else 0o644
        files[p.relative_to(path).as_posix()] = (data, mode)
    return files


def unpack(raw, part):
    with tarfile.open(fileobj=io.BytesIO(raw)) as outer:
        member = next(m for m in outer if m.name.lstrip("./") == part + ".tar.gz")
        with tarfile.open(fileobj=io.BytesIO(outer.extractfile(member).read())) as inner:
            return {m.name.lstrip("./"): (inner.extractfile(m).read(), m.mode) for m in inner if m.isfile()}


def validate(files):
    for name, (data, mode) in files.items():
        if name.endswith(".py"):
            ast.parse(data, filename=name)
        elif data.startswith((b"#!/bin/sh", b"#!/opt/bin/sh")):
            result = subprocess.run(["sh", "-n"], input=data, capture_output=True)
            if result.returncode:
                raise RuntimeError(name + ": " + result.stderr.decode())


def package(name, arch, data, ctl):
    validate(data)
    validate(ctl)
    control = ctl["control"][0].decode()
    import re
    control = re.sub(r"^Version:.*$", "Version: " + VERSIONS[name], control, flags=re.M)
    control = re.sub(r"^Installed-Size:.*$", "Installed-Size: " + str(sum(len(v[0]) for v in data.values())), control, flags=re.M)
    ctl = dict(ctl, control=(control.encode(), 0o644))
    for script in ("preinst", "postinst", "prerm", "postrm"):
        if script in ctl:
            ctl[script] = (ctl[script][0], 0o755)
    raw = archive({"debian-binary": (b"2.0\n", 0o644), "data.tar.gz": (archive(data), 0o644), "control.tar.gz": (archive(ctl), 0o644)})
    directory = DIST / ("common" if arch == "all" else arch.split("-")[0])
    directory.mkdir(parents=True, exist_ok=True)
    p = directory / (name + "_" + VERSIONS[name] + "_" + arch + ".ipk")
    p.write_bytes(raw)
    return p, data, ctl


def bot_package():
    source = tree(ROOT / "bot")
    data = {"opt/etc/telegram4pivas/" + n: v for n, v in source.items() if n != "README.md"}
    data["opt/etc/init.d/S98telegram4pivas"] = source["S98telegram4pivas"]
    postinst = '''#!/bin/sh
CFG=/opt/etc/telegram4pivas/telegram_bot_config.py
rm -f /opt/etc/telegram4pivas/.flavor
if [ ! -f "$CFG" ]; then
    umask 077
    cat > "$CFG" <<'CONFIG'
token = 'insert:API'
userid = []
reconnection_timeout = 60
reconnection_attempts = 5
version = '1.2-custom14'
CONFIG
fi
chmod 600 "$CFG"
echo 'Бот установлен; настройте pivas bot token / id и запустите pivas bot on.'
'''
    control = "Package: telegram4pivas\nVersion: 1.2-custom14\nArchitecture: all\nDepends: python3-base, python3-light, python3-requests, pivas\nInstalled-Size: 0\nDescription: Telegram controller for Pivas custom14\n"
    return package("telegram4pivas", "all", data, {"control": (control.encode(), 0o644), "postinst": (postinst.encode(), 0o755), "prerm": (b"#!/bin/sh\n/opt/etc/init.d/S98telegram4pivas stop 2>/dev/null || true\n", 0o755)})


def quic_package(arch):
    binary = ROOT / 'vendor/quic-probe' / arch
    if not binary.is_file():
        raise RuntimeError('Сначала соберите QUIC probe: python3 tools/build_quic_probe.py')
    provenance = json.loads((binary.parent / 'manifest.json').read_text())
    source = ROOT / 'tools/quic-probe'
    inputs = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()
              if p.suffix == '.go' or p.name in ('go.mod','go.sum')}
    if provenance['source_sha256'] != inputs or provenance['sha256'][arch] != hashlib.sha256(binary.read_bytes()).hexdigest():
        raise RuntimeError('QUIC probe не соответствует исходникам; пересоберите tools/build_quic_probe.py')
    notices = (binary.parent / 'LICENSES.txt').read_bytes()
    if provenance['sha256']['LICENSES.txt'] != hashlib.sha256(notices).hexdigest():
        raise RuntimeError('Повреждены лицензии QUIC probe')
    data = {'opt/sbin/pivas-quic-probe': (binary.read_bytes(), 0o755),
            'opt/share/pivas-quic-probe/LICENSES.txt': (notices, 0o644)}
    control = ('Package: pivas-quic-probe\nVersion: 0.1-1\nArchitecture: ' + arch +
               '\nInstalled-Size: 0\nDescription: One-shot Hysteria 2 QUIC certificate probe; no background service\n')
    return package('pivas-quic-probe', arch, data, {'control': (control.encode(), 0o644)})


def xray_packages(arch):
    source = ROOT / 'vendor/xray'
    provenance = json.loads((source/'manifest.json').read_text())
    if provenance['version'] != VERSIONS['xray-core'].split('-')[0]:
        raise RuntimeError('Неверная версия vendored Xray')
    record = provenance['assets'][arch]
    if arch == 'mipsel-3.4' and (record.get('origin') != 'source-build' or record.get('go') != '1.26.6'):
        raise RuntimeError('MIPS requires the Go 1.26.6 compatibility build: tools/build_xray_mips.py')
    binary = (source/arch).read_bytes()
    if hashlib.sha256(binary).hexdigest() != record['binary_sha256']:
        raise RuntimeError('Повреждён бинарник Xray: ' + arch)
    notices = {}
    for name, digest in provenance['notices'].items():
        raw = (source/name).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise RuntimeError('Повреждены лицензии Xray')
        notices['opt/share/xray/'+name] = (raw, 0o644)
    control = ('Package: xray-core\nVersion: '+VERSIONS['xray-core']+'\nArchitecture: '+arch+
               '\nDepends: ca-bundle\nProvides: xray-core-any\nLicense: MPL-2.0\n'+
               'URL: https://github.com/XTLS/Xray-core\nInstalled-Size: 0\n'+
               'Description: Xray '+provenance['version']+'; static '+arch+' binary; '+record.get('origin', 'official-release')+'\n')
    core = package('xray-core', arch, dict(notices, **{'opt/sbin/xray': (binary, 0o755)}), {'control': (control.encode(), 0o644)})
    control = ('Package: xray\nVersion: '+VERSIONS['xray']+'\nArchitecture: '+arch+
               '\nDepends: ca-bundle, xray-core (>= '+VERSIONS['xray-core']+')\nProvides: xray-any\nLicense: MPL-2.0\n'+
               'Installed-Size: 0\nDescription: Pivas Xray startup scripts and example configuration\n')
    wrapper = package('xray', arch, tree(ROOT/'xray-src/data'), {'control': (control.encode(), 0o644)})
    return core, wrapper


def installer(arch, packages):
    contents = {p.name: (p.read_bytes(), 0o644) for p in packages}
    script = '''#!/bin/sh
set -eu
case "${1:-}" in --help|-h) echo "Установка Pivas custom31 (Xray 26.3.27); запуск после настройки: pivas start"; exit 0 ;; esac
[ "$(id -u)" = 0 ] || { echo 'Нужен root'; exit 1; }
command -v opkg >/dev/null || { echo 'Нужен Entware'; exit 1; }
opkg print-architecture | awk '{print $2}' | grep -qx '@ARCH@' || { echo 'Неверная архитектура'; exit 1; }
@GUARD@
mkdir -p /opt/tmp
TMP=$(mktemp -d /opt/tmp/pivas-install.XXXXXX)
TTY_STATE=
cleanup() {
    [ -z "$TTY_STATE" ] || stty "$TTY_STATE" < /dev/tty 2>/dev/null || true
    rm -rf "$TMP"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
LINE=$(awk '/^__PAYLOAD__$/ {print NR+1; exit}' "$0")
tail -n +"$LINE" "$0" | tar -xzf - -C "$TMP"
@INSTALL@
# Also check when opkg skipped postinst because this version is already installed.
pivas repair-dns
# Interactive setup is optional. Non-interactive installs continue to accept
# environment variables and never wait for input from an SSH pipeline.
if [ -t 0 ] && command -v stty >/dev/null 2>&1; then
    TTY_STATE=$(stty -g < /dev/tty 2>/dev/null || true)
fi
ask_secret() {
    REPLY=
    [ -n "$TTY_STATE" ] || return 1
    printf '%s (Enter — пропустить): ' "$1" >&2
    stty -echo < /dev/tty || return 1
    IFS= read -r REPLY < /dev/tty || REPLY=
    stty "$TTY_STATE" < /dev/tty || true
    printf '\\n' >&2
}
ask_plain() {
    REPLY=
    [ -n "$TTY_STATE" ] || return 1
    printf '%s (Enter — пропустить): ' "$1" >&2
    IFS= read -r REPLY < /dev/tty || REPLY=
}
[ -n "${PIVAS_URL1:-}" ] || { ask_secret 'VLESS / Hysteria 2 для слота 1' && PIVAS_URL1=$REPLY || true; }
[ -n "${PIVAS_URL2:-}" ] || { ask_secret 'VLESS / Hysteria 2 для слота 2' && PIVAS_URL2=$REPLY || true; }
[ -n "${BOT_TOKEN:-}" ] || { ask_secret 'Токен Telegram-бота' && BOT_TOKEN=$REPLY || true; }
[ -n "${BOT_CHATID:-}" ] || { ask_plain 'Telegram chat ID администратора' && BOT_CHATID=$REPLY || true; }
# URLs and credentials never interpolated into shell/Python source.
[ -z "${PIVAS_URL1:-}" ] || pivas vless set-1 "$PIVAS_URL1"
[ -z "${PIVAS_URL2:-}" ] || pivas vless set-2 "$PIVAS_URL2"
[ -z "${BOT_TOKEN:-}" ] || pivas bot token "$BOT_TOKEN"
[ -z "${BOT_CHATID:-}" ] || pivas bot id "$BOT_CHATID"
echo 'Пакеты установлены. VPN оставлен на паузе; запуск: pivas start.'
echo 'Далее: pivas start; pivas bot on. Веб остаётся выключенным до pivas web pass <пароль>; pivas web on.'
exit 0
__PAYLOAD__
'''.replace("@ARCH@", arch).replace("@GUARD@", INSTALL_GUARD).replace("@INSTALL@", "\n".join('opkg install "$TMP/' + p.name + '"' for p in packages))
    p = DIST / arch.split("-")[0] / "install-pivas-full.sh"
    p.write_bytes(script.encode() + archive(contents))
    p.chmod(0o755)


def build():
    # Versioned filenames must not leave a previous release in the new manifest.
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    core = package("pivas", "all", tree(ROOT / "work/orig/data"), tree(ROOT / "work/orig/control"))
    web = package("pivas-web", "all", tree(ROOT / "web-src/data"), tree(ROOT / "web-src/control"))
    bot = bot_package()
    for arch in ("mipsel-3.4", "aarch64-3.10"):
        binary, wrapper = xray_packages(arch)
        probe = quic_package(arch)
        merged = {}
        all_parts = [binary, wrapper, probe, core, web, bot]
        dependencies = set()
        for path, data, ctl in all_parts:
            merged.update(data)
            for line in ctl["control"][0].decode().splitlines():
                if line.startswith("Depends:"):
                    dependencies.update(x.strip() for x in line.partition(":")[2].split(","))
        included = {"pivas", "pivas-web", "telegram4pivas", "xray", "xray-core", "xray-any", "xray-core-any", "pivas-quic-probe"}
        dependencies = {d for d in dependencies if d.split()[0] not in included}
        control = "Package: pivas-full\nVersion: " + VERSIONS["pivas-full"] + "\nArchitecture: " + arch + "\nDepends: " + ", ".join(sorted(dependencies)) + "\nProvides: pivas-quic-probe, pivas, pivas-web, telegram4pivas, xray, xray-core, xray-any, xray-core-any\nConflicts: pivas-quic-probe, pivas, pivas-web, telegram4pivas, xray, xray-core\nReplaces: pivas-quic-probe, pivas, pivas-web, telegram4pivas, xray, xray-core\nInstalled-Size: 0\nDescription: Pivas custom31 complete bundle; Xray 26.3.27; activation is explicit\n"
        post = "#!/bin/sh\nset -e\n"
        for part in all_parts:
            if "postinst" in part[2]:
                post += "(\n" + part[2]["postinst"][0].decode() + "\n)\n"
        full = package("pivas-full", arch, merged, {"control": (control.encode(), 0o644), "postinst": (post.encode(), 0o755), "prerm": (("#!/bin/sh\n/opt/etc/init.d/S98telegram4pivas stop 2>/dev/null || true\n/opt/etc/init.d/S99pivas-web stop 2>/dev/null || true\n" + core[2]["prerm"][0].decode()).encode(), 0o755)})
        installer(arch, [full[0]])
    sums = {}
    for p in sorted(DIST.rglob("*")):
        if p.is_file() and p.name != "manifest.json":
            sums[str(p.relative_to(DIST))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (DIST / "manifest.json").write_text(json.dumps({"versions": VERSIONS, "sha256": sums}, indent=2) + "\n")
    print("Built", len(sums), "artifacts in", DIST)


if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests"), "-q"], check=True)
    build()
