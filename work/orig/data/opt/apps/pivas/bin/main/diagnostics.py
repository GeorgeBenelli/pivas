#!/opt/bin/python3
"""Bounded, read-only diagnostic report shared by CLI, web and Telegram."""
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import performance
import dns_services

BASE = Path("/")


def redact(text):
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(r"(?i)(vless|hysteria2|hy2)://[^\s\"'<>]+", r"\1://<скрыто>", text)
    text = re.sub(r"\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b", "<uuid>", text)
    text = re.sub(r"\b\d{5,}:[A-Za-z0-9_-]{10,}\b", "<telegram-token>", text)
    text = re.sub(r"(?i)(api\.telegram\.org/(?:file/)?bot)[^/\s\"']+", r"\1<скрыто>", text)
    text = re.sub(r'''(?ix)((?:["']?\b(?:token|auth|password|passwd|pwd|secret|authorization|pbk|sid|publicKey|privateKey|id)\b["']?)\s*[=:]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;&}]+)''', r'\1"<скрыто>"', text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1<скрыто>@", text)
    return text


def tail(name, lines=60):
    try:
        with (BASE / name.lstrip("/")).open('rb') as f:
            f.seek(0, 2)
            size = f.tell();f.seek(max(0, size-65536))
            raw = f.read(65536).decode('utf-8', 'replace')
            if size > 65536:raw = raw.partition('\n')[2]
            return '\n'.join(raw.splitlines()[-lines:]) or '(пусто)'
    except FileNotFoundError:
        return "(нет файла)"


def build_report(budget=45):
    deadline = time.monotonic() + budget
    parts = ["Pivas — диагностика\n" + time.strftime("%Y-%m-%d %H:%M:%S %z") +
             "\nСодержит домены, адреса серверов, имена и MAC устройств. Проверьте файл перед передачей другим людям.\n"
             "Секреты маскируются; конфигурация авторизации и ссылки подключения не включаются."]

    def add(name, text):
        parts.append("===== " + name + " =====\n" + (text.strip() or "(пусто)"))

    def run(args, timeout=4):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "Пропущено: исчерпан общий лимит времени"
        try:
            result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=min(timeout, remaining))
            return "код=%d\n%s" % (result.returncode, (result.stdout + result.stderr)[:18000].strip())
        except subprocess.TimeoutExpired:
            return "Таймаут"
        except OSError:
            return "Команда недоступна: " + args[0]

    if deadline - time.monotonic() >= 2:
        add("CPU и память (замер 2 с)", json.dumps(performance.sample(BASE), ensure_ascii=False, indent=2))
    add("uptime", run(["uptime"]))
    packages = run(["opkg", "list-installed"])
    add("Пакеты", "\n".join(s for s in packages.splitlines() if re.search(r"код=|pivas|xray|dnsmasq|dnscrypt", s)))
    add('Файлы DNS-служб', '\n'.join(item['path'] + ': ' + item['state'] for item in dns_services.status(BASE)))
    flags = ["pivas.paused", "pivas.xray-stopped"]
    add("Режим", "\n".join(n + "=" + str((BASE / "opt/etc" / n).exists()) for n in flags))
    add("Watchdog", run(["/opt/apps/pivas/bin/main/check_dns", "status"]))
    add("Xray: проверка конфигурации", run(["xray", "run", "-test", "-confdir", "/opt/etc/xray"], 6))
    try:
        cfg = json.loads((BASE / "opt/etc/xray/pivas.json").read_text())
        slots = []
        for out in cfg.get("outbounds", []):
            if out.get("protocol") == "vless":
                for dest in out.get("settings", {}).get("vnext", []):
                    slots.append("%s: %s:%s" % (out.get("tag"), dest.get("address"), dest.get("port")))
            elif out.get('protocol') == 'hysteria':
                dest = out.get('settings', {})
                slots.append('%s: %s:%s (Hysteria 2 / QUIC)' % (out.get('tag'), dest.get('address'), dest.get('port')))
        add("Серверы слотов", "\n".join(slots))
    except (OSError, ValueError):
        add("Серверы слотов", "Конфигурация недоступна")
    for name in ("/opt/etc/pivas.list", "/opt/etc/pivas-slot2.list", "/opt/etc/pivas-groups.json", "/opt/etc/pivas-devices.json", "/opt/etc/pivas-slots.json", "/opt/etc/dnsmasq.d/pivas.servers"):
        add(name, tail(name, 150))
    for port, host in ((9753, "pivas-health.invalid"), (53, "example.com"), (9753, "example.com"),
                       (9153, secrets.token_hex(6) + ".example.com"), (9154, secrets.token_hex(6) + ".example.com")):
        add("DNS :%s %s" % (port, host), run(["dig", "+time=2", "+tries=1", "+noall", "+comments", "+answer", "+stats", "@127.0.0.1", "-p", str(port), host, "A"], 3))
    add("Процессы", run(["pidof", "xray", "dnsmasq", "dnscrypt-proxy", "dnscrypt-slot2"]))
    add("DNS-перехват", run(["iptables", "-t", "nat", "-S", "PIVAS_DNS"]))
    add("Маркировка", run(["iptables", "-t", "mangle", "-S", "PIVAS_MARK"]))
    add("Маршруты 1001", run(["ip", "route", "show", "table", "1001"]))
    add("Policy routing", run(["ip", "rule", "show"]))
    add("IpSet", run(["ipset", "list", "PIVAS_LIST", "-terse"]))
    add("Лимит числа ipset ядра", tail('/sys/module/ip_set/parameters/max_sets', 1))
    add("Наборы учёта IP", run(["ipset", "list", "-name"]))
    for name in ('/opt/etc/pivas-ip-owners.json', '/opt/etc/pivas-retired-domains.json'):
        add(name, tail(name, 150))
    for label, proxy in (("slot-1", "socks5h://127.0.0.1:1096"), ("slot-2", "socks5h://127.0.0.1:1099"), ("WAN", None)):
        cmd = ["curl", "-fsS", "--connect-timeout", "2", "--max-time", "4", "--noproxy", "" if proxy else "*"]
        if proxy: cmd += ["--proxy", proxy]
        add("Внешний IP: " + label, run(cmd + ["https://ifconfig.me/ip"], 5))
    add("Лог бота", tail("/opt/etc/telegram4pivas/telegram4pivas_log.txt", 30))
    add("Лог запуска бота", tail("/opt/tmp/telegram4pivas.log", 20))
    return redact("\n\n".join(parts) + "\n")


def main():
    target = BASE / "opt/tmp/pivas-diagnostics.lock"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a+") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Диагностика уже собирается. Дождитесь завершения")
        print(build_report(), end="")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(redact(str(exc)), file=sys.stderr)
        sys.exit(1)
