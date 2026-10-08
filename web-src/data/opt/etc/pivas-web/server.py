#!/usr/bin/env python3
"""
Минималистичный HTTP-сервер для pivas-web UI.
- Форма логина (admin + пароль из /opt/etc/pivas-web/auth) + cookie-сессия.
- HMAC-SHA256 на секрете /opt/etc/pivas-web/secret (генерится автоматически).
- CGI-скрипты из /cgi-bin/*.sh.
- Слушает ТОЛЬКО на LAN-IP br0 (наружу не торчит).
- Тот же процесс, никакой дополнительной RAM по сравнению с Basic-вариантом.
"""
import ast
import base64
import hashlib
import hmac
import http.server
import ipaddress
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, CGIHTTPRequestHandler
from pathlib import Path

# ВАЖНО: по дефолту CGIHTTPRequestHandler переключает uid на nobody перед
# exec'ом скрипта. Но pivas-файлы (/opt/etc/pivas-slot2.list, /opt/etc/xray/
# pivas.json и т.п.) имеют права 600/root — nobody не сможет их прочесть,
# state.sh вернёт битый JSON, UI покажет «сервер не настроен» и пустой
# slot-2. Возвращаем nobody → root, чтобы CGI имел доступ ко всему.
# LAN-only сервер, так что security-impact отсутствует.
http.server.nobody_uid = lambda: 0

PORT = 8888
WEBROOT = "/opt/etc/pivas-web/www"
AUTH_FILE = "/opt/etc/pivas-web/auth"
SECRET_FILE = "/opt/etc/pivas-web/secret"
SESSION_TTL = 7 * 24 * 3600   # 7 дней
COOKIE_NAME = "pivas_sess"
RUNTIME = "/opt/apps/pivas/bin/main/runtime.py"
DIAGNOSTICS = "/opt/apps/pivas/bin/main/diagnostics.py"
BOT_CONFIG = "/opt/etc/telegram4pivas/telegram_bot_config.py"
BOT_PID = "/opt/var/run/telegram4pivas.pid"


def bot_status():
    """Read bot settings without executing the Python config or exposing its token."""
    token, ids = None, []
    config = Path(BOT_CONFIG)
    if config.exists():
        source = config.read_text(encoding="utf-8")
        if len(source) > 16384:
            raise ValueError("Конфигурация бота слишком велика")
        for node in ast.parse(source).body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
                continue
            name = node.targets[0].id
            if name in ("token", "userid"):
                value = ast.literal_eval(node.value)
                if name == "token" and isinstance(value, str):
                    token = value
                elif name == "userid" and isinstance(value, list):
                    ids = [str(v) for v in value if type(v) is int]
    running = False
    try:
        pid = int(Path(BOT_PID).read_text().strip())
        running = b"/opt/etc/telegram4pivas/telegram_bot.py" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        pass
    return {"token_set": bool(token and token != "insert:API"), "ids": ids, "running": running}


def bot_command(payload):
    action = payload.get("action")
    if action == "token":
        token = payload.get("token")
        if not isinstance(token, str) or not re.fullmatch(r"[0-9]{5,15}:[A-Za-z0-9_-]{20,128}", token):
            raise ValueError("Неверный формат токена Telegram")
        args = ["pivas", "bot", "token", token]
    elif action == "ids":
        raw = payload.get("ids")
        if not isinstance(raw, str) or len(raw) > 1024:
            raise ValueError("Укажите Telegram ID числами")
        values = [v for v in re.split(r"[\s,;]+", raw.strip()) if v]
        if not 1 <= len(values) <= 20 or any(not re.fullmatch(r"-?[0-9]{1,19}", v) or abs(int(v)) > 2**63 - 1 for v in values):
            raise ValueError("Нужно от 1 до 20 Telegram ID, по одному в строке")
        ids = list(dict.fromkeys(str(int(v)) for v in values))
        args = ["pivas", "bot", "id", *ids]
    elif action in ("start", "stop"):
        args = ["pivas", "bot", "on" if action == "start" else "off"]
    else:
        raise ValueError("Неизвестное действие с ботом")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode:
        # CLI output can contain credentials or private IDs; never echo it.
        raise RuntimeError("Настройки бота не применены. Проверьте токен, ID и состояние VPN.")
    return bot_status()

def _load_secret():
    """Один раз генерим 32-байтовый секрет в /opt/etc/pivas-web/secret."""
    try:
        with open(SECRET_FILE, "rb") as f:
            data = f.read().strip()
            if len(data) >= 32:
                return data
    except Exception:
        pass
    data = secrets.token_bytes(32)
    old = os.umask(0o077)
    try:
        with open(SECRET_FILE, "wb") as f:
            f.write(data)
    except Exception:
        pass
    finally:
        os.umask(old)
    return data


SECRET = None  # Initialized on server startup; importing is side-effect free.


def load_users():
    users = {}
    try:
        with open(AUTH_FILE) as f:
            for line in f:
                line = line.strip()
                if not line or ":" not in line:
                    continue
                u, p = line.split(":", 1)
                users[u] = p
    except Exception:
        pass
    return users


def _verify_password(stored, provided):
    """
    Проверка пароля. Форматы `stored`:
      pbkdf2_sha256$<iter>$<salt_b64>$<hash_b64>   — новый
      <plain>                                       — legacy (до PBKDF2)
    Всё сравнение — через hmac.compare_digest (timing-safe).
    """
    if not stored or not provided:
        return False
    try:
        if stored.startswith("pbkdf2_sha256$"):
            _, iters, salt_b64, hash_b64 = stored.split("$", 3)
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(hash_b64)
            derived = hashlib.pbkdf2_hmac(
                "sha256", provided.encode("utf-8"), salt, int(iters)
            )
            return hmac.compare_digest(expected, derived)
    except Exception:
        return False
    # legacy plain — сравниваем безопасно, при следующем setpass
    # автоматически перейдёт на PBKDF2
    return hmac.compare_digest(stored.encode("utf-8"), provided.encode("utf-8"))


# ---- anti DNS-rebinding: проверка Host-заголовка ----
_LAN_NETS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fd00::/8"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
]


def _host_is_lan(host_header):
    """
    Разрешаем только Host: <LAN-IP>[:port]. Это закрывает DNS-rebinding:
    evil.com → 192.168.1.1 в браузере жертвы будет слать Host: evil.com,
    мы его отвергнем 403.
    """
    if not host_header:
        return False
    h = host_header.strip()
    # IPv6 в квадратных скобках: [::1]:8888
    if h.startswith("["):
        end = h.find("]")
        if end < 0:
            return False
        ip_str = h[1:end]
    else:
        ip_str = h.rsplit(":", 1)[0]
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return any(ip in net for net in _LAN_NETS)


def get_lan_ip():
    try:
        r = subprocess.run(
            ["ip", "-4", "addr", "show", "br0"],
            capture_output=True, text=True, timeout=2,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                return line.split()[1].split("/")[0]
    except Exception:
        pass
    return "192.168.1.1"


def _sign(user, exp):
    msg = f"{user}|{exp}".encode()
    mac = hmac.new(SECRET, msg, hashlib.sha256).hexdigest()
    return f"{user}|{exp}|{mac}"


def _verify(token):
    try:
        user, exp, mac = token.split("|", 2)
        exp_i = int(exp)
    except Exception:
        return None
    if exp_i < int(time.time()):
        return None
    expected = hmac.new(SECRET, f"{user}|{exp}".encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, mac):
        return None
    return user


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>pivas-web · вход</title>
<style>
:root{color-scheme:light dark;--bg:#f6f6f8;--panel:rgba(255,255,255,.8);--text:#171719;--muted:#64646f;--border:rgba(16,16,24,.12);--input:rgba(255,255,255,.65);--accent:#171719;--on-accent:#f9f9fb;--glow:rgba(16,16,24,.04)}
@media(prefers-color-scheme:dark){:root{--bg:#020203;--panel:rgba(255,255,255,.03);--text:#f4f4f6;--muted:#9a9aa6;--border:rgba(255,255,255,.14);--input:rgba(4,4,6,.55);--accent:#f4f4f6;--on-accent:#08080a;--glow:rgba(255,255,255,.08)}}
*{box-sizing:border-box}body{margin:0;min-height:100dvh;display:grid;place-items:center;padding:28px 20px;background:var(--bg);color:var(--text);font:14px/1.6 Inter,"SF Pro Text",-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased}body::before{content:"";position:fixed;inset:auto 0 0;height:46vh;background:radial-gradient(60% 80% at 50% 100%,var(--glow),transparent 70%);pointer-events:none}.box{position:relative;width:100%;max-width:410px;padding:36px 30px;border:1px solid var(--border);border-radius:18px;background:var(--panel)}.brand{display:flex;align-items:center;gap:10px;margin-bottom:40px;font-size:15px;letter-spacing:.27em;font-weight:550}.brand svg{width:28px;height:28px}h1{font-size:32px;font-weight:550;letter-spacing:-.04em;line-height:1.2;margin:0 0 12px}.sub{font-size:13px;color:var(--muted);margin:0 0 28px}label{display:block;font-size:12px;color:var(--muted);margin:16px 0 7px}input{width:100%;padding:12px 14px;border:1px solid var(--border);background:var(--input);color:var(--text);border-radius:10px;font-family:inherit;font-size:16px;line-height:1.4}input:focus{outline:2px solid var(--text);outline-offset:2px}button{width:100%;margin-top:24px;padding:12px 16px;border:1px solid var(--accent);border-radius:11px;background:var(--accent);color:var(--on-accent);font-family:inherit;font-size:14px;font-weight:500;line-height:1.4;cursor:pointer}button:hover{opacity:.88}button:focus-visible{outline:2px solid var(--accent);outline-offset:4px}.err{border:1px solid var(--text);border-radius:10px;padding:11px 12px;margin-top:16px;font-size:13px}.mini{margin:22px 0 0;text-align:center;font-size:11px;color:var(--muted)}@media(max-width:450px){.box{padding:30px 24px}}
</style>
</head>
<body>
<form class="box" method="POST" action="/login" autocomplete="on">
<div class="brand"><svg aria-hidden="true" viewBox="0 0 28 28" fill="none"><rect x="2" y="2" width="24" height="24" rx="8" stroke="currentColor"/><path d="M10 21V8h5a4 4 0 0 1 0 8h-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="20" cy="21" r="1" fill="currentColor"/></svg> PIVAS</div>
<h1>Ваша сеть — здесь.</h1>
<p class="sub">Войдите в панель управления сетью</p>
<label for="u">Логин</label>
<input id="u" name="u" value="admin" autocomplete="username" required>
<label for="p">Пароль</label>
<input id="p" name="p" type="password" autocomplete="current-password" required autofocus>
__ERR__
<button type="submit">Войти</button>
<p class="mini">Локальная панель управления · Keenetic</p>
</form>
</body>
</html>
"""


def render_login(err=""):
    block = f'<div class="err">{err}</div>' if err else ""
    return LOGIN_PAGE.replace("__ERR__", block).encode("utf-8")


class AuthCGIHandler(CGIHTTPRequestHandler):
    cgi_directories = ["/cgi-bin"]

    def log_message(self, fmt, *args):
        sys.stderr.write(
            f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}\n"
        )

    # ---------- сессии ----------

    def _get_cookie_token(self):
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        try:
            c = SimpleCookie()
            c.load(raw)
            if COOKIE_NAME in c:
                return c[COOKIE_NAME].value
        except Exception:
            pass
        return None

    def _authorized(self):
        tok = self._get_cookie_token()
        if not tok:
            return False
        user = _verify(tok)
        return user is not None

    def _set_cookie(self, token, ttl):
        # HttpOnly + SameSite=Strict, без Secure (HTTP LAN)
        v = (f"{COOKIE_NAME}={token}; Path=/; Max-Age={ttl}; "
             f"HttpOnly; SameSite=Strict")
        self.send_header("Set-Cookie", v)

    def _clear_cookie(self):
        self.send_header(
            "Set-Cookie",
            f"{COOKIE_NAME}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict",
        )

    # ---------- ответы ----------

    def _redirect(self, to):
        self.send_response(303)
        self.send_header("Location", to)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _send_login(self, err="", status=200):
        body = render_login(err)
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_form(self):
        ln = int(self.headers.get("Content-Length") or 0)
        if ln <= 0 or ln > 4096:
            return {}
        raw = self.rfile.read(ln).decode("utf-8", "replace")
        return dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))

    # ---------- маршруты ----------

    def _route_auth(self):
        """Вернёт True если запрос уже обработан (login/logout)."""
        path = self.path.split("?", 1)[0]

        if path == "/login":
            if self.command == "GET":
                self._send_login()
                return True
            if self.command == "POST":
                form = self._read_form()
                u = (form.get("u") or "").strip()
                p = form.get("p") or ""
                users = load_users()
                if p and u in users and _verify_password(users[u], p):
                    exp = int(time.time()) + SESSION_TTL
                    token = _sign(u, exp)
                    self.send_response(303)
                    self.send_header("Location", "/")
                    self._set_cookie(token, SESSION_TTL)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                else:
                    self._send_login("Неверный логин или пароль",
                                     status=401)
                return True

        if path == "/logout":
            self.send_response(303)
            self.send_header("Location", "/login")
            self._clear_cookie()
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True

        return False

    def _reject_non_lan(self):
        """Анти DNS-rebinding: 403 если Host не LAN-IP."""
        if not _host_is_lan(self.headers.get("Host")):
            body = (
                b"403 Forbidden\n\n"
                b"Host-header does not match a LAN address.\n"
                b"pivas-web is LAN-only; if you see this from a browser,\n"
                b"it's likely a DNS-rebinding attack.\n"
            )
            self.send_response(403)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return True
        return False

    def do_GET(self):
        if self._reject_non_lan():
            return
        if self._route_auth():
            return
        if not self._authorized():
            return self._redirect("/login")
        if self.path.split("?", 1)[0].startswith("/api/"):
            return self._api()
        return super().do_GET()

    def do_HEAD(self):
        if self._reject_non_lan():
            return
        if not self._authorized():
            return self._redirect("/login")
        self.send_response(405)
        self.send_header("Allow", "GET, POST")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        if self._reject_non_lan():
            return
        if self._route_auth():
            return
        if not self._authorized():
            # API-вызовы без сессии — 401 текстом (UI покажет в <pre>)
            self.send_response(401)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("Сессия истекла. Обновите страницу.\n".encode())
            return
        if self.path.split("?", 1)[0].startswith("/api/"):
            return self._api()
        return super().do_POST()

    def _json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _api(self):
        route = self.path.split("?", 1)[0]
        try:
            payload = {}
            if self.command == "POST":
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + self.headers.get("Host", ""):
                    return self._json({"error": "Запрос с другого сайта запрещён"}, 403)
                length = int(self.headers.get("Content-Length", "0"))
                limit = 6*1024*1024 if route == "/api/backup" else 262144
                if not 0 < length <= limit or self.headers.get_content_type() != "application/json":
                    return self._json({"error": "Нужен JSON размером до 256 КБ"}, 400)
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("Нужен JSON-объект")
            if route in ("/api/catalog", "/api/devices", "/api/slots"):
                args = [sys.executable, RUNTIME, route.rsplit("/", 1)[1]]
                if self.command == "POST": args += ["write"]
                elif route == "/api/devices" and urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get("refresh") == ["1"]:
                    args += ["refresh"]
                # Runtime has bounded locks/commands and owns rollback. An HTTP
                # timeout must not SIGKILL a transaction halfway through commit.
                result = subprocess.run(args, input=json.dumps(payload), capture_output=True, text=True)
                if result.returncode:
                    return self._json({"error": result.stderr.strip() or "Не удалось применить изменения"}, 409)
                value = json.loads(result.stdout)
                if route == "/api/devices":
                    client_ip = getattr(self, "client_address", ("",))[0]
                    for device in value.get("devices", []):
                        device["is_current"] = device.get("ip") == client_ip
                return self._json(value)
            if route == "/api/bot":
                return self._json(bot_status() if self.command == "GET" else bot_command(payload))
            if self.command != "POST":
                return self._json({"error": "Нужен POST"}, 405)
            if route in ("/api/backup", "/api/explain", "/api/performance"):
                result = subprocess.run([sys.executable, RUNTIME, route.rsplit("/", 1)[1]], input=json.dumps(payload), capture_output=True, text=True)
                if result.returncode:
                    return self._json({"error": "Операция не выполнена. Проверьте данные, пароль копии и состояние служб."}, 409)
                return self._json(json.loads(result.stdout))
            if route == "/api/diagnostics":
                result = subprocess.run([sys.executable, DIAGNOSTICS], capture_output=True, timeout=55)
                if result.returncode:
                    return self._json({"error": result.stderr.decode("utf-8", "replace")}, 409)
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Disposition", 'attachment; filename="pivas-diag.txt"')
                self.send_header("Content-Length", str(len(result.stdout)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(result.stdout)
                return
            if route == "/api/control":
                action, slot = payload.get("action"), payload.get("slot")
                if action in ("start", "stop"):
                    args = ["pivas", action]
                elif action == "swap":
                    args = ["pivas", "vless", "swap"]
                elif action in ("set", "rollback") and type(slot) is int and slot in (1, 2):
                    args = ["pivas", "vless", action + "-" + str(slot)]
                    if action == "set":
                        url = payload.get("url", "")
                        if not isinstance(url, str) or not url.startswith(("vless://", "hysteria2://", "hy2://")) or len(url) > 16384:
                            raise ValueError("Нужна ссылка vless://, hysteria2:// или hy2://")
                        args.append(url)
                else:
                    raise ValueError("Неизвестное действие")
                result = subprocess.run(args, capture_output=True, text=True)
                # Do not return command arguments or secret-containing CLI output.
                if result.returncode:
                    if action == 'start':
                        for line in result.stderr.splitlines():
                            if line.startswith('Pivas: DNS-службы: '):
                                return self._json({'error': line[:2000]}, 409)
                    return self._json({"error": "Команда не выполнена. Проверьте конфигурацию и диагностический отчёт"}, 409)
                if action == "swap":
                    return self._json({"ok": True, "changed": json.loads(result.stdout)["changed"]})
                return self._json({"ok": True})
            return self._json({"error": "Неизвестный endpoint"}, 404)
        except subprocess.TimeoutExpired:
            return self._json({"error": "Время ожидания истекло. Обновите состояние перед повторной операцией"}, 504)
        except RuntimeError as exc:
            return self._json({"error": str(exc)}, 409)
        except (ValueError, OSError, SyntaxError) as exc:
            return self._json({"error": str(exc)}, 400)


if __name__ == "__main__":
    os.chdir(WEBROOT)
    SECRET = _load_secret()
    # Если auth-файла нет — сервер не должен вообще стартовать,
    # это проверяет init.d-скрипт.
    ip = get_lan_ip()
    print(f"pivas-web: listening on http://{ip}:{PORT}/ (root={WEBROOT})",
          flush=True)
    # Threading нужен обязательно — CGIHTTPRequestHandler форкается, но
    # HTTP-слой всё равно однопоточный, и один «висящий» CGI (например
    # state.sh, который долго читает pivas vless N-list) блокирует login
    # и вообще всё.
    server = ThreadingHTTPServer((ip, PORT), AuthCGIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
