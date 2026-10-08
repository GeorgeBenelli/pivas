"""Named domain groups over the compatible Pivas flat lists; caller holds runtime.lock."""
import copy
import hashlib
import ipaddress
import json
import re
import secrets
from urllib.parse import urlsplit

META = "/opt/etc/pivas-groups.json"
MAIN = "/opt/etc/pivas.list"
SECOND = "/opt/etc/pivas-slot2.list"


def normalize(value):
    if not isinstance(value, str) or len(value) > 8192:
        raise ValueError("Некорректный домен")
    value = value.strip().lower()
    if value == "https://core.telegram.org/resources/cidr.txt":
        raise ValueError("Вставьте IPv4-подсети из файла Telegram, а не ссылку на файл")
    if value.startswith(("http://", "https://")):
        value = urlsplit(value).hostname or ""
    value = (value[2:] if value.startswith("*.") else value).rstrip(".")
    if "/" in value:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            raise ValueError("Некорректная подсеть: " + value) from None
        if network.version != 4:
            raise ValueError("IPv6-подсети пока не перехватываются Pivas: " + value)
        return str(network)
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("В группе нужны домены, а не IP: " + value)
    value = value.encode("idna").decode("ascii")
    if len(value) > 253 or "." not in value or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", s) for s in value.split(".")):
        raise ValueError("Некорректный домен: " + value)
    return value


def parse_domains(values):
    if isinstance(values, str):
        values = re.split(r"[\s,;]+", re.sub(r"#.*", "", values))
    if not isinstance(values, list) or len(values) > 2000:
        raise ValueError("Не более 2000 доменов за операцию")
    result = sorted({normalize(v) for v in values if v})
    if not result:
        raise ValueError("Добавьте хотя бы один домен")
    return result


def revision(rt):
    digest = hashlib.sha256()
    for name in (META, MAIN, SECOND):
        data = rt.path(name).read_bytes() if rt.path(name).exists() else b""
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def load(rt):
    raw = json.loads(rt.path(META).read_text()) if rt.path(META).exists() else {"version": 1, "groups": []}
    if not isinstance(raw, dict) or raw.get("version") != 1 or not isinstance(raw.get("groups"), list):
        raise ValueError("Неизвестный формат pivas-groups.json")
    groups = raw["groups"]
    if len(groups)>200:raise ValueError("Не более 200 групп")
    owned, ids, names = set(), set(), set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("name"), str) or not 1<=len(group["name"].strip())<=80 or any(ord(c)<32 for c in group["name"]):
            raise ValueError("Повреждена запись группы")
        if type(group.get("slot")) is not int or group.get("slot") not in (1, 2) or type(group.get("enabled")) is not bool or not re.fullmatch(r"[a-f0-9]{16}", str(group.get("id", ""))):
            raise ValueError("Некорректные параметры группы")
        values = parse_domains(group.get("domains"))
        if owned.intersection(values) or group["id"] in ids or group["name"].casefold() in names:
            raise ValueError("Конфликт групп в pivas-groups.json")
        owned.update(values); ids.add(group["id"]); names.add(group["name"].casefold())
        group["domains"] = values
    if len(owned)>10000:raise ValueError("Не более 10000 доменов в группах")
    return groups


def view(rt):
    groups = load(rt)
    main, second = rt.entries(MAIN), rt.entries(SECOND)
    owned = {d for g in groups for d in g["domains"]}
    drift = []
    for g in groups:
        for d in g["domains"]:
            actual = 2 if d in second else 1 if d in main else 0
            if actual != (g["slot"] if g["enabled"] else 0):
                drift.append(d)
    return {"revision": revision(rt), "groups": groups,
            "standalone": [{"domain": d, "slot": 2 if d in second else 1} for d in sorted((main | second) - owned)],
            "drift": drift}


def handle(rt, request=None):
    state = view(rt)
    if request is None:
        return state
    if not isinstance(request, dict) or request.get("revision") != state["revision"]:
        raise ValueError("Список изменился в другой вкладке. Обновите страницу и повторите")
    if state["drift"]:
        raise ValueError("Списки изменены вне управления группами. Восстановите согласованность pivas-groups.json и списков")
    groups = copy.deepcopy(state["groups"])
    standalone = {v["domain"]: v["slot"] for v in state["standalone"]}
    action = request.get("action")
    group = next((g for g in groups if g["id"] == request.get("id")), None)
    if action in ("update", "move", "toggle", "delete") and group is None:
        raise ValueError("Группа не найдена")
    if action in ("create", "update"):
        name = request.get("name", "")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or any(ord(c) < 32 for c in name):
            raise ValueError("Название группы: от 1 до 80 символов")
        name = name.strip()
        if any(g["name"].casefold() == name.casefold() and g is not group for g in groups):
            raise ValueError("Группа с таким названием уже есть")
        values = parse_domains(request.get("domains", []))
        if group is None:
            if len(groups) >= 200:
                raise ValueError("Достигнут лимит 200 групп")
            group = {"id": secrets.token_hex(8), "enabled": True}
            groups.append(group)
        for other in groups:
            if other is not group and set(other["domains"]).intersection(values):
                raise ValueError("Домен уже принадлежит группе «" + other["name"] + "»")
        group.update(name=name, domains=values, slot=request.get("slot"))
        for d in values:
            standalone.pop(d, None)  # Existing individual entries are explicitly adopted.
    elif action == "move":
        group["slot"] = request.get("slot")
    elif action == "toggle":
        if type(request.get("enabled")) is not bool:
            raise ValueError("Нужен флаг enabled")
        group["enabled"] = request["enabled"]
    elif action == "delete":
        groups.remove(group)
    elif action in ("domain-add", "domain-delete"):
        values = parse_domains(request.get("domains", []))
        for g in groups:
            if set(g["domains"]).intersection(values):
                raise ValueError("Управляйте этим доменом через группу «" + g["name"] + "»")
        for d in values:
            if action == "domain-add": standalone[d] = request.get("slot")
            else: standalone.pop(d, None)
    else:
        raise ValueError("Неизвестное действие")
    if any(type(s) is not int or s not in (1, 2) for s in list(standalone.values()) + [g["slot"] for g in groups]):
        raise ValueError("Укажите слот 1 или 2")
    if sum(len(g["domains"]) for g in groups) + len(standalone) > 10000:
        raise ValueError("Не более 10000 записей в группах и отдельных доменах")
    effective = dict(standalone)
    for g in groups:
        if g["enabled"]:
            effective.update({d: g["slot"] for d in g["domains"]})
    main, second = set(effective), {d for d, s in effective.items() if s == 2}
    changes = {META: (json.dumps({"version": 1, "groups": groups}, ensure_ascii=False, indent=2) + "\n").encode(),
               MAIN: ("\n".join(sorted(main)) + ("\n" if main else "")).encode(),
               SECOND: ("\n".join(sorted(second)) + ("\n" if second else "")).encode()}
    if main == rt.entries(MAIN) and second == rt.entries(SECOND):
        # Names and disabled-group edits do not change network policy.
        rt.transaction({META: changes[META]}, lambda c: None, lambda c, t: None)
    else:
        rt.apply(list_values=(main, second), extra_changes=changes)
    rt.path("/opt/tmp/pivas-web-state.json").unlink(missing_ok=True)
    return view(rt)


def guard_legacy(rt, args):
    groups = load(rt)
    if not groups or not args:
        return
    # Bulk legacy replacements have no group semantics: fail before touching files.
    if args[0] in ("clear", "purge", "import", "reset", "init", "update"):
        raise ValueError("Есть группы доменов: используйте управление группами; массовая замена старого списка отключена")
    values = args[1:] if args[0] in ("add", "new", "del", "rm") else args[2:] if len(args) > 1 and args[0] == "vless" and re.fullmatch(r"[12]-(add|del)", args[1]) else []
    for raw in values:
        try:
            domain = normalize(raw)
        except (ValueError, UnicodeError):
            continue
        for group in groups:
            if domain in group["domains"] or (domain.startswith("www.") and domain[4:] in group["domains"]):
                raise ValueError("Домен «" + domain + "» входит в группу «" + group["name"] + "». Измените группу целиком")


def cli(rt, args):
    if not args or args[0] == "list":
        return view(rt)
    action = args[0]
    request = {"action": action, "revision": revision(rt)}
    if action == "create" and len(args) >= 4:
        request.update(name=args[1], slot=int(args[2]), domains=args[3:])
    elif action == "update" and len(args) >= 5:
        request.update(id=args[1], name=args[2], slot=int(args[3]), domains=args[4:])
    elif action == "move" and len(args) == 3:
        request.update(id=args[1], slot=int(args[2]))
    elif action in ("enable", "disable", "delete") and len(args) == 2:
        request.update(id=args[1])
        if action != "delete": request.update(action="toggle", enabled=action == "enable")
    else:
        raise ValueError("pivas group list | create NAME SLOT DOMAIN... | update ID NAME SLOT DOMAIN... | move ID SLOT | enable/disable/delete ID")
    return handle(rt, request)
