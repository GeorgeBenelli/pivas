"""Device discovery through local Keenetic RCI and MAC-based Pivas bypass.

All public operations run under runtime.lock. Only Pivas-owned chains change.
"""
import hashlib
import ipaddress
import json
import re
import secrets
import shlex
import socket
import struct
import subprocess
import time

CONFIG = "/opt/etc/pivas-devices.json"
MAX_EXCLUDED = 10
CACHE = "/opt/tmp/pivas-hosts-cache.json"
TAG = "pivas-device-bypass"
CHAINS = (("nat", "PIVAS_DNS"), ("mangle", "PIVAS_MARK"))
IPT = "/opt/sbin/iptables"
RESTORE = "/opt/sbin/iptables-restore"


def mac(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}", value):
        raise ValueError("Некорректный MAC-адрес")
    value = value.upper()
    if value == "00:00:00:00:00:00" or int(value[:2], 16) & 1:
        raise ValueError("Нужен MAC конкретного устройства")
    return value


def revision(rt):
    return hashlib.sha256(rt.path(CONFIG).read_bytes() if rt.path(CONFIG).exists() else b"").hexdigest()


def load(rt):
    if not rt.path(CONFIG).exists():
        return {}
    data = json.loads(rt.path(CONFIG).read_text())
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("excluded"), dict):
        raise ValueError("Повреждён pivas-devices.json")
    if len(data["excluded"]) > 256:
        raise ValueError("Не более 256 исключённых устройств")
    return {mac(k): str(v)[:120] for k, v in data["excluded"].items()}


def discover(rt, refresh=False):
    cache = rt.path(CACHE)
    if not refresh:
        try:
            saved = json.loads(cache.read_text())
            if 0 <= time.time() - saved["at"] < 15:
                return saved["hosts"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    result = rt.command("curl", "--noproxy", "*", "-fsS", "--max-time", "5", "http://127.0.0.1:79/rci/show/ip/hotspot", timeout=7)
    data = json.loads(result.stdout)
    if not isinstance(data, dict) or not isinstance(data.get("host"), (list, dict)):
        raise ValueError("Keenetic вернул неизвестный формат списка устройств")
    hosts = data["host"]
    if isinstance(hosts, dict):
        hosts = [hosts] if "mac" in hosts else list(hosts.values())
    found = {}
    for host in hosts:
        if not isinstance(host, dict):
            continue
        try:
            address = mac(host.get("mac"))
        except ValueError:
            continue
        try:
            ip = str(ipaddress.IPv4Address(host.get("ip", "")))
            if ip == "0.0.0.0": ip = ""
        except ipaddress.AddressValueError:
            ip = ""
        interface = host.get("interface", {})
        if isinstance(interface, dict):
            interface = interface.get("description") or interface.get("name") or interface.get("id") or ""
        active = host.get("active")
        online = active in (True, "yes", "true", "up", 1) if active is not None else host.get("link") == "up"
        found[address] = {"mac": address, "ip": ip,
                          "name": str(host.get("name") or host.get("hostname") or address)[:120],
                          "interface": str(interface)[:120], "online": online,
                          "registered": host.get("registered") in (True, "yes", "true", 1)}
    try:
        rt.atomic(cache, json.dumps({"at": time.time(), "hosts": found}).encode())
    except OSError:
        pass
    return found


def rules(excluded, table, chain):
    if (table, chain) not in CHAINS:
        raise ValueError("Изменять можно только цепочки Pivas")
    result = []
    for address in sorted(excluded):
        # Some Keenetic kernels have xt_mac but not xt_comment. Identify these
        # exact rules by their Pivas-only chain, MAC match and target instead.
        prefix = "-A %s -m mac --mac-source %s" % (chain, mac(address))
        if table == "mangle":
            # Clear only Pivas bits; preserve Keenetic connection policy marks.
            result += [prefix + " -j MARK --set-xmark 0x0/0xd1000",
                       prefix + " -j CONNMARK --set-xmark 0x0/0xd1000"]
        result.append(prefix + " -j RETURN")
    return result


def owned(line):
    args = shlex.split(line)
    if "--comment" in args:
        pos = args.index("--comment")
        return pos + 1 < len(args) and args[pos + 1] == TAG
    if len(args) < 8 or args[0] != "-A" or args[1] not in ("PIVAS_DNS", "PIVAS_MARK"):
        return False
    if args[2:5] != ["-m", "mac", "--mac-source"]:
        return False
    try:
        mac(args[5])
    except ValueError:
        return False
    tail = args[6:]
    if tail == ["-j", "RETURN"]:
        return True
    if args[1] != "PIVAS_MARK" or len(tail) != 4 or tail[:1] != ["-j"] or tail[2] != "--set-xmark":
        return False
    if tail[1] not in ("MARK", "CONNMARK"):
        return False
    try:
        value, mask = tail[3].split("/", 1)
        return int(value, 0) == 0 and int(mask, 0) == 0xd1000
    except (ValueError, TypeError):
        return False


def script(table, chain, before, after):
    """Update only our MAC rules; never reparse unrelated router rules.

    iptables -S can print an existing rule in a form that this router's
    iptables-restore cannot replay. Deleting and inserting our own rules
    leaves those rules in place and keeps each table commit atomic.
    """
    lines = ["*" + table]
    for line in before:
        if owned(line):
            lines.append("-D " + line[3:])
    for position, line in enumerate(after, 1):
        if owned(line):
            lines.append("-I %s %s %s" % (chain, position, line[len("-A " + chain + " "):]))
    return "\n".join(lines + ["COMMIT", ""])


def firewall(rt, excluded, target=None):
    targets = [tuple(target)] if target else CHAINS
    if any(t not in CHAINS for t in targets):
        raise ValueError("Неизвестная цепочка")
    pending = []
    for table, chain in targets:
        result = rt.command(IPT, "-w", "-t", table, "-S", chain, check=False)
        if result.returncode:
            # An absent chain is normal while stopped/before first setup. Do
            # not treat permission/tool errors as absence.
            all_chains = rt.command(IPT, "-w", "-t", table, "-S").stdout
            if re.search(r"^-N " + re.escape(chain) + r"$", all_chains, re.M):
                raise RuntimeError("Не удалось прочитать цепочку " + chain)
            continue
        before = [line for line in result.stdout.splitlines() if line.startswith("-A " + chain + " ")]
        after = rules(excluded, table, chain) + [line for line in before if not owned(line)]
        if [shlex.split(s) for s in before] != [shlex.split(s) for s in after]:
            pending.append((table, chain, before, after))
    # Validate both table updates before committing either one.
    for table, chain, before, after in pending:
        rt.command(RESTORE, "--test", "--noflush", input_text=script(table, chain, before, after))
    applied = []
    try:
        for table, chain, before, after in pending:
            rt.command(RESTORE, "--noflush", input_text=script(table, chain, before, after))
            applied.append((table, chain, before))
    except BaseException:
        for table, chain, before in reversed(applied):
            current = rt.command(IPT, "-w", "-t", table, "-S", chain).stdout
            current = [line for line in current.splitlines() if line.startswith("-A " + chain + " ")]
            rt.command(RESTORE, "--noflush", input_text=script(table, chain, current, before))
        raise


def refresh_connections(rt, host):
    if not host or not host["online"] or not host["ip"]:
        return "Если устройство уже было подключено, переподключите его к сети для сброса старых соединений."
    # Verify current ownership before deleting conntrack entries for an IP.
    neighbour = rt.command("ip", "-4", "neigh", "show", "to", host["ip"], check=False).stdout.upper()
    if not re.search(r"\bLLADDR\s+" + re.escape(host["mac"]) + r"\b", neighbour):
        return "Правило сохранено. Переподключите устройство к сети, чтобы сбросить прежние соединения."
    try:
        for options in (("--mark", "0xd1000/0xd1000"), ("-p", "udp", "--dport", "53"), ("-p", "tcp", "--dport", "53")):
            result = rt.command("conntrack", "-D", "-f", "ipv4", "--orig-src", host["ip"], *options, check=False, timeout=5)
            if result.returncode and "0 flow entries" not in result.stderr:
                return "Правило сохранено. Для старых соединений переподключите устройство к сети."
    except (OSError, RuntimeError):
        return "Правило сохранено. Для старых соединений переподключите устройство к сети (conntrack недоступен)."
    return ""


def warm_domains(rt):
    """Relearn active IPs when a formerly bypassed client may cache DNS.

    dnsmasq fills PIVAS_LIST from replies, but a client's cached answer never
    reaches dnsmasq. Querying the active names locally closes that gap without
    restarting DNS, Xray or touching another device's connections.
    """
    try:
        values = rt.entries("/opt/etc/pivas.list") | rt.entries("/opt/etc/pivas-slot2.list")
        names = sorted(rt.domains(values))
        if not names:
            return ""
        port = int(rt.config().get("DNSMASQ_PORT", "9753"))
        if not 1 <= port <= 65535:
            raise ValueError("Неверный порт DNS Pivas")
        # One toggle must not generate an unbounded burst on a small router.
        selected = names[:128]
        pending = {}
        start_id = secrets.randbelow(65536)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.bind(("127.0.0.1", 0))
            deadline = time.monotonic() + 8
            next_index = 0
            failed = 0
            while next_index < len(selected) or pending:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                # A small in-flight window avoids flooding DNSCrypt on routers
                # with many domains, while the global deadline bounds the UI.
                while next_index < len(selected) and len(pending) < 4:
                    name = selected[next_index]
                    query_id = (start_id + next_index) & 0xffff
                    question = b"".join(bytes((len(label),)) + label.encode("ascii") for label in name.split(".")) + b"\0"
                    packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + question + struct.pack("!HH", 1, 1)
                    client.sendto(packet, ("127.0.0.1", port))
                    pending[query_id] = time.monotonic()
                    next_index += 1
                oldest = min(pending.values())
                client.settimeout(min(remaining, max(0.01, oldest + 1.5 - time.monotonic())))
                try:
                    reply, sender = client.recvfrom(4096)
                except socket.timeout:
                    # The timeout is set to the oldest request's deadline.
                    oldest_id = min(pending, key=pending.get)
                    pending.pop(oldest_id)
                    failed += 1
                    continue
                if sender != ("127.0.0.1", port) or len(reply) < 12:
                    continue
                query_id, flags, _, _, _, _ = struct.unpack("!HHHHHH", reply[:12])
                if query_id in pending and flags & 0x8000:
                    # Some valid parent domains have no A record themselves;
                    # dnsmasq can still learn their subdomains on client lookup.
                    if (flags & 0xf) in (0, 3):
                        pending.pop(query_id)
                    else:
                        pending.pop(query_id)
                        failed += 1
        if pending or next_index < len(selected) or len(names) > len(selected) or failed:
            count = failed + len(pending) + len(names) - next_index
            return "Pivas включён, но DNS-прогрев неполный ({}/{} доменов). Переподключите устройство или повторите проверку после обновления DNS.".format(count, len(names))
    except (OSError, ValueError, UnicodeError, AttributeError):
        return "Pivas включён, но DNS-адреса сайтов не обновились. Переподключите устройство или повторите проверку после обновления DNS."
    return ""


def handle(rt, request=None, refresh=False):
    excluded, rev = load(rt), revision(rt)
    warning = ""
    try:
        hosts = discover(rt, refresh=refresh or request is not None)
    except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired):
        hosts = {}
        warning = "Не удалось получить устройства Keenetic. Сохранённые исключения продолжают действовать."
    if request is not None:
        if not isinstance(request, dict) or request.get("revision") != rev:
            raise ValueError("Исключения изменились. Обновите список устройств")
        address = mac(request.get("mac"))
        if type(request.get("excluded")) is not bool:
            raise ValueError("Нужно указать excluded: true/false")
        if request["excluded"] and address not in hosts and address not in excluded:
            raise ValueError("Сначала получите устройство из списка Keenetic")
        new = dict(excluded)
        if request["excluded"]: new[address] = hosts.get(address, {}).get("name", excluded.get(address, address))
        else: new.pop(address, None)
        if len(new) > MAX_EXCLUDED and len(new) >= len(excluded):
            raise ValueError("Можно исключить не более 10 устройств")
        if new != excluded:
            firewall(rt, new)
            try:
                rt.atomic(rt.path(CONFIG), (json.dumps({"version": 1, "excluded": new}, ensure_ascii=False, indent=2) + "\n").encode())
            except BaseException:
                firewall(rt, excluded)
                raise
            # A failed reconnect is not a failed saved policy; show it explicitly.
            if not request["excluded"]:
                warning = warm_domains(rt)
            try:
                reconnect_warning = refresh_connections(rt, hosts.get(address))
                warning = " ".join(part for part in (warning, reconnect_warning) if part)
            except Exception:
                warning = "Правило сохранено. Переподключите устройство для сброса старых соединений."
            excluded, rev = new, revision(rt)
    for address, name in excluded.items():
        hosts.setdefault(address, {"mac": address, "name": name, "ip": "", "interface": "", "online": False, "registered": False})
    for host in hosts.values():
        host["excluded"] = host["mac"] in excluded
    return {"revision": rev, "devices": sorted(hosts.values(), key=lambda h: (not h["excluded"], not h["online"], h["name"].casefold())), "warning": warning, "limit": MAX_EXCLUDED}
