#!/opt/bin/python3
"""Shared lock and transactional DNS/routing configuration for Pivas.

Importing this module has no side effects. Router commands run only from main().
"""
import contextlib
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import signal
import subprocess
import sys
import tempfile
import time

BASE = Path("/")
# Quad9's official IPv4 DoH endpoint on port 443. The previous DNSCrypt
# endpoint on 8443 was unreachable through some VPN exits.
STAMP = "sdns://AgMAAAAAAAAABzkuOS45LjkgsBkgdEu7dsmrBT4B4Ht-BQ5HPSD3n3vqQ1-v5DydJC8SZG5zOS5xdWFkOS5uZXQ6NDQzCi9kbnMtcXVlcnk"


class Busy(RuntimeError):
    pass


def path(name):
    return BASE / name.lstrip("/")


def command(*args, timeout=25, check=True, input_text=None):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input_text)
    if check and result.returncode:
        raise RuntimeError("{}: {}".format(args[0], result.stderr.strip() or result.stdout.strip()))
    return result


@contextlib.contextmanager
def lock(nonblocking=False):
    target = path("/opt/tmp/pivas-mutation.lock")
    target.parent.mkdir(parents=True, exist_ok=True)
    inherited = os.environ.get("PIVAS_LOCK_TOKEN")
    reentrant = False
    if inherited:
        try:
            owner = json.loads(target.read_text())
            if owner.get("token") == inherited:
                os.kill(owner["pid"], 0)
                reentrant = True
        except (OSError, ValueError, KeyError):
            pass
    if reentrant:
        yield
        return
    with target.open("a+") as handle:
        deadline = time.monotonic() + (0 if nonblocking else 90)
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy("Другая операция Pivas ещё выполняется")
                time.sleep(0.1)
        token = secrets.token_hex(16)
        handle.seek(0)
        handle.truncate()
        json.dump({"pid": os.getpid(), "token": token}, handle)
        handle.flush()
        old = os.environ.get("PIVAS_LOCK_TOKEN")
        os.environ["PIVAS_LOCK_TOKEN"] = token
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("PIVAS_LOCK_TOKEN", None)
            else:
                os.environ["PIVAS_LOCK_TOKEN"] = old
            handle.seek(0)
            handle.truncate()
            handle.flush()


def config():
    values = {}
    for line in path("/opt/etc/pivas.conf").read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.startswith("#"):
            values[key.strip()] = value.strip()
    return values


def entries(filename):
    p = path(filename)
    result = set()
    if not p.exists():
        return result
    return parse_entries(p.read_text())


def parse_entries(text):
    result = set()
    for raw in text.splitlines():
        item = raw.partition("#")[0].strip().lower().lstrip("*.").rstrip(".")
        if not item or item.startswith("-"):
            continue
        try:
            result.add(str(ipaddress.ip_network(item, strict=False)))
            continue
        except ValueError:
            pass
        # The legacy list also accepts IPv4 ranges; these are consumed by ipset.
        if re.fullmatch(r"[0-9.]+-[0-9.]+", item):
            lo, hi = item.split("-")
            result.update(str(n) for n in ipaddress.summarize_address_range(
                ipaddress.IPv4Address(lo), ipaddress.IPv4Address(hi)))
            continue
        item = item.encode("idna").decode("ascii")
        if len(item) > 253 or "." not in item or any(
            not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in item.split(".")
        ):
            raise ValueError("Некорректная запись списка: " + raw)
        result.add(item)
    return result


def domains(values):
    return {v for v in values if "/" not in v}


def upstreams(cfg):
    # Native Keenetic DNS follows WAN changes. It uses ISP DNS only when the
    # router itself is configured to obtain DNS from its provider.
    values = re.split(r"[,;\s]+", cfg.get("DNS_PROVIDER", "").strip())
    if not any(values):
        values = ["127.0.0.1#53"]
    checked = []
    for value in filter(None, values):
        host, sep, port = value.partition("#")
        address = ipaddress.ip_address(host)
        if address.version != 4:
            raise ValueError("DNS_PROVIDER пока поддерживает IPv4")
        port = int(port) if sep else 53
        if not 1 <= port <= 65535:
            raise ValueError("Некорректный порт DNS_PROVIDER")
        if address.is_loopback and port in (9153, 9154, int(cfg.get("DNS_CRYPT_PORT", "9153")), int(cfg.get("DNSMASQ_PORT", "9753"))):
            raise ValueError("DNS_PROVIDER создаёт петлю через Pivas")
        checked.append(str(address) + "#" + str(port))
    return checked


def servers(cfg, main, second, down1=False, down2=False):
    provider = upstreams(cfg)
    policy = cfg.get("DNS_VPN_FAILURE", "closed")
    fallback2 = cfg.get("DNS_SLOT2_FALLBACK", "policy")
    if policy not in ("provider", "closed") or fallback2 not in ("policy", "slot1"):
        raise ValueError("Некорректная политика отказа DNS")
    fallback = [] if policy == "closed" else provider
    first = fallback if down1 else ["127.0.0.1#" + cfg.get("DNS_CRYPT_PORT", "9153")]
    other = (first if fallback2 == "slot1" else fallback) if down2 else ["127.0.0.1#9154"]
    output = ["# generated by Pivas; edit pivas.conf / domain lists"]
    output += ["server=" + p for p in provider]
    for values, targets in ((domains(main - second), first), (domains(second), other)):
        for d in sorted(values):
            # Explicit fallback overrides a parent suffix belonging to another slot.
            output += ["server=/" + d + "/" + p for p in (targets or [""])]
    return "\n".join(output) + "\n"


def routing(original, main, second, retired=None):
    from vless_url import migrate_config
    result = migrate_config(original)
    inbounds = result.setdefault("inbounds", [])
    for tag, port in (("pivas-dns-1", 1096), ("pivas-slot2-direct", 1099)):
        inbound = next((i for i in inbounds if i.get("tag") == tag), None)
        if inbound is None:
            inbound = {}
            inbounds.append(inbound)
        inbound.update(tag=tag, port=port, listen="127.0.0.1", protocol="socks", settings={"udp": False, "auth": "noauth"})
        inbound.pop("sniffing", None)
    rules = [
        {"type": "field", "inboundTag": [tag], "outboundTag": slot}
        for tag, slot in (("pivas-dns-1", "slot-1"), ("pivas-bot", "slot-2"), ("pivas-slot2-direct", "slot-2"))
    ]
    # Both slots participate so a child in slot-1 can override its slot-2 parent.
    slot2_domains = domains(second)
    exceptions = {d for d in domains(main - second) if any(".".join(d.split(".")[n:]) in slot2_domains for n in range(1, len(d.split("."))))}
    for d in sorted(slot2_domains | exceptions, key=lambda d: (-d.count("."), d)):
        rules.append({"type": "field", "domain": ["domain:" + d], "outboundTag": "slot-2" if d in second else "slot-1"})
    slot2_networks = [ipaddress.ip_network(n) for n in second if "/" in n]
    slot1_networks = [ipaddress.ip_network(n) for n in main - second if "/" in n]
    networks = sorted(slot2_networks + [n for n in slot1_networks if any(n.version == parent.version and n.subnet_of(parent) for parent in slot2_networks)], key=lambda n: (-n.prefixlen, str(n)))
    for n in networks:
        rules.append({"type": "field", "ip": [str(n)], "outboundTag": "slot-2" if str(n) in second else "slot-1"})
    retired = retired or []
    if retired:
        if not any(o.get("tag") == "pivas-direct" for o in result["outbounds"]):
            result["outbounds"].append({"tag": "pivas-direct", "protocol": "freedom"})
        # Specific active children precede retired parents; control inbounds stay first.
        domain_rules = [{"type":"field", "domain":["domain:"+d], "outboundTag": "pivas-direct" if d in retired else "slot-2" if d in second else "slot-1"}
                        for d in sorted(domains(main) | set(retired), key=lambda d: (-d.count("."), d))]
        rules = [r for r in rules if "domain" not in r]
        rules[3:3] = domain_rules
    # Preserve rules unrelated to Pivas's reserved outbounds/inbounds.
    old = result.setdefault("routing", {}).get("rules", [])
    rules += [r for r in old if r.get("outboundTag") not in ("slot-1", "slot-2", "pivas-direct") and not set(r.get("inboundTag", [])) & {"pivas-dns-1", "pivas-bot", "pivas-slot2-direct"}]
    result["routing"]["rules"] = rules
    if not any(o.get("tag") == "slot-1" for o in result.get("outbounds", [])):
        raise ValueError("В Xray отсутствует outbound slot-1")
    result["outbounds"].sort(key=lambda o: 0 if o.get("tag") == "slot-1" else 1)
    return result


def dnscrypt(cfg, slot):
    stamp = cfg.get("DNS_STAMP_" + str(slot), "") or STAMP
    if not re.fullmatch(r"sdns://[A-Za-z0-9_-]+", stamp):
        raise ValueError("DNS_STAMP должен быть корректным stamp sdns://")
    port = int(cfg.get("DNS_CRYPT_PORT", "9153")) if slot == 1 else 9154
    return """# generated by Pivas; configure DNS_STAMP_1 / DNS_STAMP_2 in pivas.conf
listen_addresses = ['127.0.0.1:%d']
server_names = ['pivas-resolver']
ipv4_servers = true
ipv6_servers = false
block_ipv6 = true
dnscrypt_servers = true
doh_servers = true
odoh_servers = false
force_tcp = true
proxy = 'socks5://127.0.0.1:%d'
timeout = 5000
keepalive = 30
log_level = 2
use_syslog = true
cert_refresh_delay = 240
netprobe_timeout = 0
cache = true
cache_size = 1024
cache_min_ttl = 30
cache_max_ttl = 600
cache_neg_min_ttl = 1
cache_neg_max_ttl = 30
ignore_system_dns = true
bootstrap_resolvers = ['127.0.0.1:53']
[static.'pivas-resolver']
stamp = '%s'
""" % (port, 1096 if slot == 1 else 1099, stamp)


def atomic(filename, content):
    filename.parent.mkdir(parents=True, exist_ok=True)
    mode = filename.stat().st_mode & 0o777 if filename.exists() else 0o600
    # dnsmasq reopens this file after dropping privileges on SIGHUP.
    if str(filename).endswith("/dnsmasq.d/pivas.servers"):
        mode = 0o644
    fd, temp = tempfile.mkstemp(prefix="." + filename.name + ".", dir=filename.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(temp, mode)
        os.replace(temp, filename)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def live(service):
    return "alive" in command(service, "status", check=False).stdout


def transaction(changes, validate, activate, rollback=None):
    snapshots = {name: path(name).read_bytes() if path(name).exists() else None for name in changes}
    changed = {n: b for n, b in changes.items() if snapshots[n] != b}
    if changed:
        validate(changes)
    touched = []
    try:
        for name, data in changed.items():
            if data is None:
                path(name).unlink(missing_ok=True)
            else:
                atomic(path(name), data)
        activate(set(changed), touched)
    except BaseException:
        for name in changed:
            before = snapshots[name]
            if before is None:
                path(name).unlink(missing_ok=True)
            else:
                atomic(path(name), before)
        rollback_error = None
        if rollback is not None:
            try: rollback()
            except Exception as exc: rollback_error = exc
        for service, was_alive in reversed(touched):
            try: command(service, "restart" if was_alive else "stop", check=False)
            except Exception as exc: rollback_error = rollback_error or exc
        if rollback_error:
            raise RuntimeError("Ошибка применения и неполный откат; проверьте диагностический отчёт") from rollback_error
        raise
    return bool(changed)


XRAY = "/opt/etc/xray/pivas.json"
SERVERS = "/opt/etc/dnsmasq.d/pivas.servers"
IPSETS = "/opt/etc/dnsmasq.d/pivas.dnsmasq"
DNSMASQ = "/opt/etc/dnsmasq.conf"
TOML1 = "/opt/etc/dnscrypt-proxy.toml"
TOML2 = "/opt/etc/dnscrypt-proxy-slot2.toml"
S1 = "/opt/etc/init.d/S09dnscrypt-proxy2"
S2 = "/opt/etc/init.d/S10dnscrypt-slot2"
SX = "/opt/etc/init.d/S24xray"
SD = "/opt/etc/init.d/S56dnsmasq"
PENDING = "/opt/tmp/pivas-dns.pending"


def validate_xray(data):
    with tempfile.TemporaryDirectory(prefix="pivas-xray-", dir=path("/opt/tmp")) as td:
        (Path(td) / "pivas.json").write_bytes(data)
        for f in path("/opt/etc/xray").glob("*.json"):
            (Path(td) / f.name).write_bytes(data if f.name == "pivas.json" else f.read_bytes())
        command("/opt/sbin/xray", "run", "-test", "-confdir", td)


def swap_slots():
    """Swap complete connection profiles, keeping slot tags and routing in place.

    Caller holds lock. Backups and the two outbounds form one transaction.
    """
    if not path(XRAY).exists():
        raise ValueError("Сначала настройте оба слота")
    original = path(XRAY).read_bytes()
    from vless_url import migrate_config
    config = migrate_config(json.loads(original))
    slots = {}
    for number in (1, 2):
        tag = "slot-" + str(number)
        found = [o for o in config.get("outbounds", []) if o.get("tag") == tag]
        if len(found) != 1:
            raise ValueError("Нужны два настроенных слота")
        outbound = found[0]
        from vless_url import configured
        if not configured(outbound):
            raise ValueError("Сначала настройте оба слота")
        slots[tag] = outbound
    if {k: v for k, v in slots["slot-1"].items() if k != "tag"} == {k: v for k, v in slots["slot-2"].items() if k != "tag"}:
        return False
    config["outbounds"] = [dict(slots["slot-2" if o["tag"] == "slot-1" else "slot-1"], tag=o["tag"])
                           if o.get("tag") in slots else o for o in config["outbounds"]]
    backup = "/opt/etc/.pivas/backup/pivas.json"
    changes = {XRAY: (json.dumps(config, indent=2) + "\n").encode(), backup: original}
    for number in (1, 2):
        changes[backup + ".slot" + str(number)] = (json.dumps({"outbound": slots["slot-" + str(number)]}, indent=2) + "\n").encode()

    def activate(changed, touched):
        if not path("/opt/etc/pivas.xray-stopped").exists() and live(SX):
            touched.append((SX, True))
            command(SX, "restart")
            if not live(SX):
                raise RuntimeError("Xray не запустился после обмена ссылками")
    try:
        return transaction(changes, lambda c: validate_xray(c[XRAY]), activate)
    finally:
        path("/opt/tmp/pivas-web-state.json").unlink(missing_ok=True)


def apply(routing_only=False, list_values=None, extra_changes=None, config_values=None, xray_value=None, activate_extra=None, rollback_extra=None):
    cfg = config() if config_values is None else config_values
    if list_values is None and path("/opt/etc/pivas-groups.json").exists():
        import catalog
        if catalog.view(sys.modules[__name__])["drift"]:
            raise ValueError("Изменение нарушает состав группы. Используйте pivas group или веб-панель")
    main, second = list_values if list_values is not None else (entries("/opt/etc/pivas.list"), entries("/opt/etc/pivas-slot2.list"))
    if not path(XRAY).exists() and xray_value is None:
        raise RuntimeError("Сначала задайте VLESS: pivas vless set-1 <ссылка>")
    import ownership
    removed_domains = ownership.retired(sys.modules[__name__], main | second)
    xray = (json.dumps(routing(json.loads(path(XRAY).read_text()) if xray_value is None else xray_value, main, second, removed_domains), indent=2) + "\n").encode()
    if routing_only:
        return transaction({XRAY: xray}, lambda c: validate_xray(c[XRAY]), lambda c, t: None)
    conf = path(DNSMASQ).read_text() if path(DNSMASQ).exists() else path("/opt/apps/pivas/etc/conf/dnsmasq.conf").read_text()
    lines = []
    for line in conf.splitlines():
        clean = line.strip()
        if (clean.startswith("server=") and not clean.startswith("server=/")) or clean.startswith("servers-file=/opt/etc/dnsmasq.d/pivas"):
            continue
        if clean.startswith("address=/pivas-health.invalid/"):
            continue
        if re.match(r"^(port|listen-address|interface|cache-size|no-resolv|no-poll)\s*(=|$)", clean) or "@" in clean:
            continue
        lines.append(line)
    lan = cfg.get("LAN_INTERFACE", "br0")
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]+", lan):
        raise ValueError("Некорректный LAN_INTERFACE")
    port = int(cfg.get("DNSMASQ_PORT", "9753"))
    if port == 53 or not 1024 <= port <= 65535:
        raise ValueError("DNSMASQ_PORT должен быть отдельным непривилегированным портом")
    if not any(x.startswith("conf-dir=") for x in lines):
        lines.append("conf-dir=/opt/etc/dnsmasq.d/,*.dnsmasq")
    lines += ["port=" + str(port), "listen-address=127.0.0.1", "interface=" + lan,
              "no-resolv", "no-poll", "cache-size=0", "servers-file=" + SERVERS,
              "address=/pivas-health.invalid/192.0.2.1"]
    changes = {
        XRAY: xray,
        SERVERS: servers(cfg, main, second, path("/opt/tmp/pivas-slot1-dns.down").exists(), path("/opt/tmp/pivas-slot2-dns.down").exists()).encode(),
        IPSETS: ownership.compile_rules(main | second).encode(),
        ownership.RETIRED: json.dumps(removed_domains).encode(),
        DNSMASQ: ("\n".join(lines) + "\n").encode(),
        TOML1: dnscrypt(cfg, 1).encode(),
        TOML2: dnscrypt(cfg, 2).encode(),
        "/opt/etc/dnsmasq.d/pivas-slot2.servers": None,
        "/opt/etc/dnsmasq.d/pivas-slot2-dns.dnsmasq": None,
    }
    owner_before = ownership.load(sys.modules[__name__])
    desired_owners = {"domains": sorted(domains(main | second)), "networks": sorted(n for n in main | second if "/" in n)}
    if path("/opt/etc/pivas.paused").exists():
        desired_owners = {key: sorted(set(value) | set(owner_before.get(key, []))) for key, value in desired_owners.items()}
    changes[ownership.STATE] = json.dumps(desired_owners, sort_keys=True).encode()
    changes.update(extra_changes or {})
    pending = set(json.loads(path(PENDING).read_text())) if path(PENDING).exists() else set()
    paused = path("/opt/etc/pivas.paused").exists()
    if paused:
        pending |= {n for n in (DNSMASQ, SERVERS, IPSETS, TOML1, TOML2)
                    if not path(n).exists() or path(n).read_bytes() != changes[n]}
        changes[PENDING] = json.dumps(sorted(pending)).encode() if pending else None
    else:
        changes[PENDING] = None

    def validate(c):
        validate_xray(c[XRAY])
        with tempfile.TemporaryDirectory(prefix="pivas-dns-", dir=path("/opt/tmp")) as td:
            td = Path(td)
            (td / "servers").write_bytes(c[SERVERS])
            dropins = td / "dnsmasq.d"
            dropins.mkdir()
            for f in path("/opt/etc/dnsmasq.d").glob("*.dnsmasq"):
                if f.name not in ("pivas.dnsmasq", "pivas-slot2-dns.dnsmasq"):
                    (dropins / f.name).write_bytes(f.read_bytes())
            (dropins / "pivas.dnsmasq").write_bytes(c[IPSETS])
            staged = c[DNSMASQ].replace(SERVERS.encode(), str(td / "servers").encode())
            staged = staged.replace(b"/opt/etc/dnsmasq.d/", (str(dropins) + "/").encode())
            (td / "dnsmasq.conf").write_bytes(staged)
            command("/opt/sbin/dnsmasq", "--test", "--conf-file=" + str(td / "dnsmasq.conf"))
            for slot in (1, 2):
                f = td / ("dnscrypt" + str(slot) + ".toml")
                f.write_bytes(c[TOML1 if slot == 1 else TOML2])
                command("/opt/sbin/dnscrypt-proxy", "-check", "-config", str(f))

    ownership_snapshot = None
    ownership_result = {}
    def activate(changed, touched):
        nonlocal ownership_snapshot, ownership_result
        changed |= pending
        def restart(service):
            alive = live(service)
            touched.append((service, alive))
            command(service, "restart" if alive else "start")
            if not live(service):
                raise RuntimeError("Служба не запустилась: " + service)
        if XRAY in changed and live(SX):
            restart(SX)
        if paused:
            if activate_extra: activate_extra()
            return
        if TOML1 in changed or not live(S1):
            restart(S1)
        if second:
            if TOML2 in changed or not live(S2):
                restart(S2)
        elif live(S2):
            touched.append((S2, True))
            command(S2, "stop")
        if ownership_created or {DNSMASQ, IPSETS, ownership.STATE}.intersection(changed) or not live(SD):
            alive = live(SD)
            touched.append((SD, alive))
            command(SD, "stop")
            ownership_snapshot = ownership.snapshot(sys.modules[__name__])
            ownership_result = ownership.reconcile(sys.modules[__name__], owner_before, main | second)
            command(SD, "start")
            if not live(SD):
                raise RuntimeError("dnsmasq не запустился")
        elif SERVERS in changed:
            pids = command("pidof", "dnsmasq").stdout.split()
            if len(pids) != 1:
                raise RuntimeError("Нужен один процесс dnsmasq для безопасного HUP")
            command("kill", "-HUP", pids[0])
        if activate_extra: activate_extra()
    backup = path("/opt/etc/pivas-backup/pre-custom2-dns.json")
    if not backup.exists():
        atomic(backup, json.dumps({n: path(n).read_text() if path(n).exists() else None for n in changes}, ensure_ascii=False, indent=2).encode())
    ownership_created = ownership.prepare(sys.modules[__name__], main | second)
    def rollback_ownership():
        error = None
        if ownership_snapshot is not None:
            try:
                command(SD, "stop", check=False)
                ownership.undo(sys.modules[__name__], ownership_snapshot)
            except Exception as exc: error = exc
        if rollback_extra:
            try: rollback_extra()
            except Exception as exc: error = error or exc
        if error: raise error
    changed = transaction(changes, validate, activate, rollback_ownership)
    if not paused:
        ownership.finish(sys.modules[__name__], owner_before, main | second, ownership_result)
    return changed


def native_dns():
    cfg = config()
    # Explicit upstreams are a user choice; do not rewrite Keenetic's policy.
    targets = upstreams(cfg)
    if not cfg.get("DNS_PROVIDER"):
        result = command("curl", "--noproxy", "*", "-fsS", "-m", "5", "-d", '[{"opkg":{"no dns-override":true}},{"system":{"configuration":{"save":true}}}]', "localhost:79/rci/")
        response = json.loads(result.stdout)
        def errors(value):
            if isinstance(value, dict):
                return value.get("status") == "error" or "error" in value or any(errors(v) for v in value.values())
            return isinstance(value, list) and any(errors(v) for v in value)
        if errors(response):
            raise RuntimeError("Keenetic не подтвердил включение штатного DNS")
    for target in targets:
        host, port = target.split("#")
        response = command("dig", "+time=2", "+tries=1", "+short", "@" + host, "-p", port, "example.com", "A", check=False)
        if any(re.fullmatch(r"[0-9]+(?:\.[0-9]+){3}", s) for s in response.stdout.splitlines()):
            return
    raise RuntimeError("Обычный DNS не отвечает; перехват Pivas не включён")


def prepare():
    """Configure the existing VLESS/Proxy21 mode without interactive legacy setup."""
    if not path("/opt/etc/pivas.paused").exists():
        raise RuntimeError("Подготовка требует паузы: pivas stop")
    for binary in ("/opt/sbin/xray", "/opt/sbin/dnsmasq", "/opt/sbin/dnscrypt-proxy"):
        if not path(binary).is_file():
            raise RuntimeError("Не установлен обязательный компонент: " + binary)
    interfaces = json.loads(command("curl", "--noproxy", "*", "-fsS", "-m", "5", "http://127.0.0.1:79/rci/show/interface").stdout)
    if not isinstance(interfaces, dict):
        raise RuntimeError("Неизвестный формат списка интерфейсов Keenetic")
    existing = interfaces.get("Proxy21")
    if existing and existing.get("description") != "Pivas-proxy-vless":
        raise RuntimeError("Proxy21 занят другим подключением; автоматическая замена запрещена")
    payload = [{"interface": {"name": "Proxy21", "description": "Pivas-proxy-vless", "proxy": {
        "protocol": {"proto": "socks5"}, "upstream": {"host": "127.0.0.1", "port": "1097"}, "socks5-udp": True}}},
        {"interface": {"name": "Proxy21", "up": True}}, {"system": {"configuration": {"save": True}}}]
    result = command("curl", "--noproxy", "*", "-fsS", "-m", "5", "-d", json.dumps(payload), "http://127.0.0.1:79/rci/")
    if re.search(r'"(?:status"\s*:\s*"error|error"\s*:)', result.stdout):
        raise RuntimeError("Keenetic отклонил Proxy21; проверьте наличие компонента Proxy client")
    conf = path("/opt/etc/pivas.conf").read_text()
    for key, value in {"INFACE_CLI":"Proxy21", "INFACE_ENT":"Pivas-proxy-vless", "SETUP_FINISHED":"yes"}.items():
        conf = re.sub(r"^" + key + r"=.*\n?", "", conf, flags=re.M) + key + "=" + value + "\n"
    atomic(path("/opt/etc/pivas.conf"), conf.encode())
    mapping = path("/opt/etc/inface_equals")
    lines = mapping.read_text().splitlines() if mapping.exists() else []
    lines = [s for s in lines if not s.startswith("Proxy21|")]
    atomic(mapping, ("\n".join(lines + ["Proxy21|Pivas-proxy-vless"]) + "\n").encode())
    path("/opt/etc/hosts").touch(exist_ok=True)


def detach():
    # All managed jumps, including guest-LAN variants left by older versions.
    for table, chain in (("nat", "PIVAS_DNS"), ("mangle", "PIVAS_MARK")):
        output = command("/opt/sbin/iptables", "-w", "-t", table, "-S", "PREROUTING").stdout
        for line in output.splitlines():
            words = shlex.split(line)
            if words[:2] == ["-A", "PREROUTING"] and "-j" in words and words[words.index("-j") + 1] == chain:
                command("/opt/sbin/iptables", "-w", "-t", table, "-D", *words[1:])
        command("/opt/sbin/iptables", "-w", "-t", table, "-F", chain, check=False)
        command("/opt/sbin/iptables", "-w", "-t", table, "-X", chain, check=False)

    # Older Pivas builds marked Telegram directly in PREROUTING, outside
    # PIVAS_MARK. Remove only rules that use the legacy PIVAS_TG set.
    output = command("/opt/sbin/iptables", "-w", "-t", "mangle", "-S", "PREROUTING").stdout
    for line in output.splitlines():
        words = shlex.split(line)
        if words[:2] == ["-A", "PREROUTING"] and "--match-set" in words:
            index = words.index("--match-set")
            if words[index + 1:index + 2] == ["PIVAS_TG"]:
                command("/opt/sbin/iptables", "-w", "-t", "mangle", "-D", *words[1:])
    for name in ("PIVAS_TG", "PIVAS_LIST", "PIVAS_DESTINATION_EXCLUDED"):
        command("/opt/sbin/ipset", "destroy", name, check=False)
    # The same old Telegram rule installed a dedicated policy route. Do not
    # clear table 1002 wholesale: remove only this exact legacy mark/rule.
    output = command("/opt/sbin/ip", "rule", "show").stdout
    for line in output.splitlines():
        match = re.fullmatch(r"\s*(\d+):\s+from all fwmark 0xd2000/0xd2000 lookup 1002\s*", line)
        if match:
            command("/opt/sbin/ip", "rule", "del", "fwmark", "0xd2000/0xd2000",
                    "lookup", "1002", "priority", match.group(1))


def remove_proxy():
    interfaces = json.loads(command("curl", "--noproxy", "*", "-fsS", "-m", "5", "http://127.0.0.1:79/rci/show/interface").stdout)
    if not isinstance(interfaces, dict):
        raise RuntimeError("Неизвестный ответ Keenetic; интерфейсы не удалены")
    owned = {"Proxy21": {"Pivas-proxy-vless"},
             "Proxy22": {"Pivas-proxy-vless", "Pivas-slot2-direct"}}
    names = [n for n, descriptions in owned.items()
             if interfaces.get(n, {}).get("description") in descriptions]
    if names:
        payload = [{"interface": {"name": n, "no": True}} for n in names]
        payload.append({"system": {"configuration": {"save": True}}})
        result = command("curl", "--noproxy", "*", "-fsS", "-m", "5", "-d", json.dumps(payload), "http://127.0.0.1:79/rci/")
        if re.search(r'"(?:status"\s*:\s*"error|error"\s*:)', result.stdout):
            raise RuntimeError("Keenetic отклонил удаление интерфейса Pivas")
        mapping = path("/opt/etc/inface_equals")
        if mapping.exists():
            lines = [line for line in mapping.read_text().splitlines()
                     if not (line.split("|", 1)[0] in names and "Pivas-" in line)]
            atomic(mapping, ("\n".join(lines) + ("\n" if lines else "")).encode())


def execute_locked(args):
    """Rollback files and running services if a legacy list/VLESS command fails."""
    if len(args) > 1 and Path(args[0]).name == "pivas":
        import catalog
        catalog.guard_legacy(sys.modules[__name__], args[1:])
    mutation = len(args) > 1 and Path(args[0]).name == "pivas" and (
        args[1] in ("add", "new", "del", "rm", "import", "clear", "purge") or
        (args[1] == "vless" and len(args) > 2 and re.match(r"(?:set|rollback|[12]-(?:add|del))", args[2]))
    )
    if mutation:
        import ownership
        if not path(ownership.STATE).exists():
            atomic(path(ownership.STATE), json.dumps(ownership.load(sys.modules[__name__])).encode())
    watched = ["/opt/etc/pivas-ip-owners.json", "/opt/etc/pivas-retired-domains.json", XRAY, SERVERS, DNSMASQ, TOML1, TOML2, PENDING, "/opt/etc/pivas.list", "/opt/etc/pivas-slot2.list",
               "/opt/etc/dnsmasq.d/pivas.dnsmasq"]
    watched += ["/opt/etc/.pivas/backup/pivas.json" + suffix for suffix in ("", ".slot1", ".slot2")]
    before = {n: path(n).read_bytes() if path(n).exists() else None for n in watched} if mutation else {}
    services = {s: live(s) for s in (SX, S1, S2, SD) if path(s).exists()} if mutation else {}
    child_env = dict(os.environ, PIVAS_LOCK_ENTRY=args[0])
    child = subprocess.Popen(args, env=child_env)
    old_handlers = {}
    def forward(sig, frame):
        child.send_signal(sig)
    for sig in (signal.SIGTERM, signal.SIGINT):
        old_handlers[sig] = signal.signal(sig, forward)
    try:
        status = child.wait()
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)
    if status and before:
        changed = set()
        for name, data in before.items():
            current = path(name).read_bytes() if path(name).exists() else None
            if data == current:
                continue
            changed.add(name)
            if data is None:
                path(name).unlink(missing_ok=True)
            else:
                atomic(path(name), data)
        for service, configs in ((SX, [XRAY]), (S1, [TOML1]), (S2, [TOML2]), (SD, [DNSMASQ, SERVERS, "/opt/etc/dnsmasq.d/pivas.dnsmasq"])):
            if changed.intersection(configs) and service in services:
                command(service, "restart" if services[service] else "stop", check=False)
        if changed:
            print("Pivas: ошибка применения; предыдущие конфигурации восстановлены", file=sys.stderr)
    if mutation:
        path("/opt/tmp/pivas-web-state.json").unlink(missing_ok=True)
    return status


def main(args):
    if not args:
        raise ValueError("Нужна команда: apply, routing, native-dns, locked")
    with lock(nonblocking=args[0] == "try-locked"):
        if args == ['dns-services']:
            import dns_services
            restored = dns_services.ensure(BASE)
            for name in restored:
                print('Восстановлен файл DNS-службы: ' + name)
            print('DNS-службы: необходимые файлы на месте.')
            return 0
        if args in (["explain"], ["performance"]):
            module = __import__(args[0])
            print(json.dumps(module.handle(sys.modules[__name__], json.load(sys.stdin)), ensure_ascii=False))
            return 0
        if args == ["backup"]:
            import backup
            print(json.dumps(backup.handle(sys.modules[__name__], json.load(sys.stdin)), ensure_ascii=False))
            return 0
        if args == ["swap-slots"]:
            print(json.dumps({"changed": swap_slots()}))
            return 0
        if args[0] == "slots":
            import slots
            rt = sys.modules[__name__]
            value = slots.handle(rt, json.load(sys.stdin)) if args[1:] == ['write'] else slots.cli(rt, args[1:])
            print(json.dumps(value, ensure_ascii=False))
            return 0
        if args[0] in ("locked", "try-locked"):
            return execute_locked(args[1:])
        if args[0] == "devices":
            import devices
            rt = sys.modules[__name__]
            if len(args) > 1 and args[1] == "rules":
                print("\n".join(devices.rules(devices.load(rt), *args[2:])))
            elif len(args) > 1 and args[1] == "firewall":
                devices.firewall(rt, devices.load(rt), args[2:] or None)
            elif len(args) > 1 and args[1] == "write":
                print(json.dumps(devices.handle(rt, json.load(sys.stdin)), ensure_ascii=False))
            elif len(args) > 1 and args[1] in ("exclude", "include"):
                if len(args) != 3:
                    raise ValueError("pivas devices exclude/include <MAC>")
                print(json.dumps(devices.handle(rt, {"revision": devices.revision(rt), "mac": args[2], "excluded": args[1] == "exclude"}), ensure_ascii=False))
            elif len(args) == 1 or args[1:] in (["list"], ["refresh"]):
                print(json.dumps(devices.handle(rt, refresh=args[1:] == ["refresh"]), ensure_ascii=False))
            else:
                raise ValueError("pivas devices list | exclude/include <MAC>")
            return 0
        if args[0] == "catalog":
            import catalog
            request = json.load(sys.stdin) if len(args) > 1 and args[1] == "write" else None
            print(json.dumps(catalog.handle(sys.modules[__name__], request), ensure_ascii=False))
            return 0
        if args[0] == "group":
            import catalog
            print(json.dumps(catalog.cli(sys.modules[__name__], args[1:]), ensure_ascii=False, indent=2))
            return 0
        if args[0] == "native-dns":
            native_dns()
        elif args[0] == "prepare":
            prepare()
        elif args[0] == "detach":
            detach()
        elif args[0] == "remove-proxy":
            remove_proxy()
        elif args[0] in ("apply", "routing"):
            print("changed" if apply(args[0] == "routing") else "ok")
        else:
            raise ValueError("Неизвестная команда: " + args[0])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except Busy as exc:
        if len(sys.argv) > 1 and sys.argv[1] == "try-locked":
            sys.exit(0)  # NDM callbacks must never wait on their own RCI caller.
        print("Pivas: " + str(exc), file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print("Pivas: " + str(exc), file=sys.stderr)
        sys.exit(1)
