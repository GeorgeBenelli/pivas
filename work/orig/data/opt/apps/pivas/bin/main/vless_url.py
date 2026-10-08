"""VLESS and Hysteria 2 import/export shared by CLI, web and Telegram.

Only supported transport combinations are accepted. Errors never echo a URL.
"""
import copy
import json
import math
from pathlib import Path
import re
import shlex
import sys
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit


def boolean(value, name):
    if value.lower() in ("1", "true", "yes"):
        return True
    if value.lower() in ("0", "false", "no", ""):
        return False
    raise ValueError("Некорректное значение " + name)


def parse(link):
    if isinstance(link, str) and link.startswith(('hysteria2://', 'hy2://')):
        return parse_hysteria(link)
    if not isinstance(link, str) or not link.startswith("vless://"):
        raise ValueError("Поддерживаются vless://, hysteria2:// и hy2://")
    if len(link) > 16384 or re.search(r"[\x00-\x20\x7f]", link):
        raise ValueError("В ссылке есть пробелы/управляющие символы или она слишком длинная")
    if re.search(r"%(?![0-9a-fA-F]{2})", link):
        raise ValueError("Некорректное URL-кодирование")
    try:
        url = urlsplit(link)
        address, port = url.hostname, url.port
        user = unquote(url.username or "", errors="strict")
        pairs = parse_qsl(url.query, keep_blank_values=True, errors="strict")
    except (ValueError, UnicodeError):
        raise ValueError("Не удалось разобрать адрес, порт или URL-кодирование") from None
    if not user or url.password is not None or re.search(r"[\s\x00-\x1f\x7f@]", user):
        raise ValueError("Нужен корректный идентификатор пользователя перед @")
    if not address or url.path not in ("", "/") or re.search(r"[\s\x00-\x1f\x7f]", address):
        raise ValueError("Нужен адрес сервера после @")
    if port is None or not 1 <= port <= 65535:
        raise ValueError("Порт должен быть числом от 1 до 65535")
    params = {}
    for key, value in pairs:
        if re.search(r"[\x00-\x1f\x7f]", key + value):
            raise ValueError("В параметрах ссылки есть управляющие символы")
        if key in params and params[key] != value:
            raise ValueError("В ссылке повторён параметр с разными значениями")
        params[key] = value
    network = params.get("type", "tcp")
    if network == 'splithttp':
        network = 'xhttp'
    security = params.get("security", "reality")
    if (network, security) not in (("tcp", "reality"), ("raw", "reality"), ("grpc", "tls"), ('xhttp', 'tls'), ('xhttp', 'reality')):
        raise ValueError("Поддерживаются TCP + Reality, gRPC + TLS и XHTTP + TLS/Reality")
    if params.get("encryption", "none") != "none":
        raise ValueError("Поддерживается только encryption=none")
    tls_request(link)
    sni = params.get("sni") or params.get("serverName") or address
    stream = {"network": network, "security": security}
    if network == 'xhttp' and params.get('flow'):
        raise ValueError('XHTTP требует пустой flow; xtls-rprx-vision с XHTTP несовместим')
    if security == "reality":
        public_key = params.get("pbk", "")
        if not public_key:
            raise ValueError("Для Reality обязателен параметр pbk (publicKey)")
        short_id = params.get("sid", "")
        if not re.fullmatch(r"(?:[0-9a-fA-F]{2}){0,8}", short_id):
            raise ValueError("Reality sid должен содержать до 16 hex-символов чётной длины")
        flow = params.get("flow", "" if network == 'xhttp' else "xtls-rprx-vision")
        stream["realitySettings"] = {
            "serverName": sni, "publicKey": public_key,
            "fingerprint": params.get("fp") or "firefox",
            "shortId": short_id, "spiderX": params.get("spx", params.get("spiderX", "/")),
        }
        if network != 'xhttp':
            stream["tcpSettings"] = {"header": {"type": "none"}}
    else:
        flow = params.get("flow", "")
        if flow:
            raise ValueError("gRPC + TLS требует пустой flow; xtls-rprx-vision с gRPC несовместим")
        alpn = [v.strip() for v in params.get("alpn", "h2").split(",") if v.strip()]
        if network == 'grpc' and (not alpn or alpn[0] != "h2"):
            raise ValueError("Для gRPC первым протоколом alpn должен быть h2")
        if network == 'xhttp' and (not alpn or any(p not in ('h2', 'http/1.1', 'h3') for p in alpn)):
            raise ValueError('Для XHTTP поддерживаются alpn=h2, http/1.1 или h3')
        tls = {"serverName": sni, "alpn": alpn}
        if tls_request(link) is False:
            tls["allowInsecure"] = False  # Preserve strict policy in exported links.
        pins = params.get("pcs") or params.get("pinnedPeerCertSha256")
        if pins:
            tls["pinnedPeerCertSha256"] = normalize_pins(pins)
        if params.get("vcn"):
            tls["verifyPeerCertByName"] = params["vcn"]
        if params.get("fp"):
            tls["fingerprint"] = params["fp"]
        stream["tlsSettings"] = tls
        if network == 'grpc':
            mode = params.get("mode", "gun")
            if mode not in ("", "gun", "multi"):
                raise ValueError("Для gRPC поддерживаются mode=gun и mode=multi")
            multi = boolean(params.get("multiMode", str(mode == "multi")), "multiMode")
            if "mode" in params and "multiMode" in params and multi != (mode == "multi"):
                raise ValueError("Параметры mode и multiMode противоречат друг другу")
            stream["grpcSettings"] = {"serviceName": params.get("serviceName", ""), "multiMode": multi}
            if params.get("authority"):
                stream["grpcSettings"]["authority"] = params["authority"]
    if network == 'xhttp':
        mode = params.get('mode') or 'auto'
        if mode not in ('auto', 'packet-up', 'stream-up', 'stream-one'):
            raise ValueError('Режим XHTTP: auto, packet-up, stream-up или stream-one')
        path = params.get('path', '/')
        if not path.startswith('/') or len(path) > 4096:
            raise ValueError('Путь XHTTP должен начинаться с / и быть не длиннее 4096 символов')
        host = params.get('host', '')
        if len(host) > 512 or re.search(r'[\s/@?#]', host):
            raise ValueError('Некорректный host XHTTP')
        stream['xhttpSettings'] = {'path': path, 'host': host, 'mode': mode}
        if params.get('extra'):
            stream['xhttpSettings']['extra'] = xhttp_extra(params['extra'])
    return {"protocol": "vless", "settings": {"vnext": [{"address": address, "port": port,
            "users": [{"id": user, "encryption": "none", "flow": flow}]}]}, "streamSettings": stream}


def xhttp_extra(raw):
    """Keep native XHTTP options, rejecting malformed/bounded JSON before saving."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError()
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError()
    try:
        if len(raw.encode()) > 8192:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError()
        allowed = {'headers', 'xPaddingBytes', 'xPaddingObfsMode', 'xPaddingKey', 'xPaddingHeader',
                   'xPaddingPlacement', 'xPaddingMethod', 'uplinkHTTPMethod', 'sessionPlacement',
                   'sessionKey', 'seqPlacement', 'seqKey', 'uplinkDataPlacement', 'uplinkDataKey',
                   'uplinkChunkSize', 'noGRPCHeader', 'noSSEHeader', 'scMaxEachPostBytes',
                   'scMinPostsIntervalMs', 'scMaxBufferedPosts', 'scStreamUpServerSecs',
                   'serverMaxHeaderBytes', 'xmux', 'downloadSettings'}
        if set(value) - allowed:
            raise ValueError()
        nodes = [0]
        def bounded(node, depth=0):
            nodes[0] += 1
            if depth > 12 or nodes[0] > 512:
                raise ValueError()
            if isinstance(node, dict):
                if node.get('allowInsecure'):
                    raise ValueError()
                for key, item in node.items():
                    bounded(key, depth + 1); bounded(item, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    bounded(item, depth + 1)
            elif isinstance(node, str) and re.search(r'[\x00-\x1f\x7f]', node):
                raise ValueError()
            elif isinstance(node, float) and not math.isfinite(node):
                raise ValueError()
        bounded(value)
        return value
    except (ValueError, RecursionError, UnicodeError):
        raise ValueError('extra XHTTP: нужен JSON-объект параметров Xray 26.3.27 без повторов, управляющих символов и allowInsecure=true (до 8 КБ)') from None


def migrate_config(config):
    """Convert pre-26.3.27 Hysteria options without changing endpoints or secrets."""
    result = copy.deepcopy(config)
    outbounds = result.get('outbounds', [])
    if 'outbound' in result:
        outbounds = [result['outbound']]
    for out in outbounds:
        stream = out.get('streamSettings', {})
        if out.get('protocol') != 'hysteria' and stream.get('network') != 'hysteria':
            continue
        hy = stream.get('hysteriaSettings', {})
        old = {key: hy[key] for key in ('up', 'down', 'congestion', 'udphop') if key in hy}
        if not old:
            continue
        quic = stream.setdefault('finalmask', {}).setdefault('quicParams', {})
        for key, value in old.items():
            target = {'up': 'brutalUp', 'down': 'brutalDown', 'congestion': 'congestion', 'udphop': 'udpHop'}[key]
            if key == 'udphop':
                value = dict(value)
                if 'port' in value:
                    value['ports'] = value.pop('port')
            if target in quic and quic[target] != value:
                raise ValueError('Конфликт старых и новых параметров Hysteria; конфигурация не изменена')
            quic[target] = value
            hy.pop(key)
    return result


def migrate_file(filename):
    import runtime
    with runtime.lock():
        path = Path(filename)
        if not path.is_file():
            return False
        original = path.read_bytes()
        current = json.loads(original)
        migrated = migrate_config(current)
        if migrated == current:
            return False
        data = (json.dumps(migrated, indent=2, ensure_ascii=False) + '\n').encode()
        runtime.validate_xray(data)
        backup = path.with_name(path.name + '.pre-xray-26.3.27')
        if not backup.exists():
            runtime.atomic(backup, original)
        backup.chmod(0o600)
        path.chmod(0o600)
        runtime.atomic(path, data)
        return True


def hopping_ports(value):
    """Validate a bounded port list without expanding ranges in memory."""
    parts = value.split(',')
    if not 1 <= len(parts) <= 32:
        raise ValueError('Не более 32 портов/диапазонов Hysteria 2')
    for part in parts:
        if not re.fullmatch(r'[0-9]{1,5}(?:-[0-9]{1,5})?', part):
            raise ValueError('Порты Hysteria 2: число или диапазон через запятую')
        ends = [int(v) for v in part.split('-')]
        if any(not 1 <= v <= 65535 for v in ends) or ends[0] > ends[-1]:
            raise ValueError('Некорректный диапазон портов Hysteria 2')
    return int(parts[0].split('-')[0])


def parse_hysteria(link):
    if len(link) > 8192 or re.search(r'[\x00-\x20\x7f]', link) or re.search(r'%(?![0-9a-fA-F]{2})', link):
        raise ValueError('Некорректное URL-кодирование или длина ссылки Hysteria 2')
    try:
        url = urlsplit(link)
        if url.path not in ('', '/'):
            raise ValueError()
        raw_auth, server = url.netloc.rsplit('@', 1) if '@' in url.netloc else ('', url.netloc)
        auth = unquote(raw_auth, errors='strict')
        if server.startswith('['):
            host, tail = server[1:].split(']', 1)
            import ipaddress
            ipaddress.IPv6Address(host)
            if tail and not tail.startswith(':'):
                raise ValueError()
            ports = tail[1:] if tail else '443'
        else:
            if server.count(':') > 1:
                raise ValueError()
            host, sep, ports = server.partition(':')
            ports = ports if sep else '443'
        if len(auth.encode()) > 4096 or re.search(r'[\x00-\x1f\x7f]', auth):
            raise ValueError()
        if not host or re.search(r'[\s\x00-\x1f\x7f/@?#%]', host):
            raise ValueError()
        pairs = parse_qsl(url.query, keep_blank_values=True, errors='strict')
    except (ValueError, UnicodeError):
        raise ValueError('Нужны корректные пароль, сервер и порт Hysteria 2') from None
    params = {}
    for key, val in pairs:
        if re.search(r'[\x00-\x1f\x7f]', key + val) or (key in params and params[key] != val):
            raise ValueError('Некорректный или повторённый параметр Hysteria 2')
        params[key] = val
    if any(params.get(key) for key in ('ech', 'realm', 'realm.token')):
        raise ValueError('ECH и Realm в Hysteria 2 пока не поддерживаются нашим Xray')
    if params.get('obfs', '') not in ('', 'none', 'salamander'):
        raise ValueError('Поддерживается маскировка Hysteria 2 Salamander')
    obfs = params.get('obfs-password', '')
    if len(obfs.encode()) > 4096:
        raise ValueError('Пароль Salamander слишком длинный')
    if params.get('obfs') == 'salamander' and len(obfs.encode()) < 4:
        raise ValueError('Для Salamander нужен obfs-password длиной от 4 байт')
    if obfs and params.get('obfs') != 'salamander':
        raise ValueError('obfs-password требует obfs=salamander')
    port = hopping_ports(ports)
    hop = params.get('mport') or (ports if ',' in ports or '-' in ports else '')
    if hop:
        hopping_ports(hop)
    policy = tls_request(link)
    tls = {'serverName': params.get('sni') or host, 'alpn': ['h3']}
    if re.search(r'[\s\x00-\x1f\x7f]', tls['serverName']):
        raise ValueError('Некорректное имя TLS-сервера Hysteria 2')
    if policy is False:
        tls['allowInsecure'] = False
    pin = params.get('pinSHA256') or params.get('pcs') or params.get('pinnedPeerCertSha256')
    if pin:
        tls['pinnedPeerCertSha256'] = normalize_pins(pin)
    hy = {'version': 2, 'auth': auth}
    for key in ('up', 'down'):
        rate = params.get(key) or (params.get(key + 'mbps', '') + ' mbps' if params.get(key + 'mbps') else '')
        if rate:
            if not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?\s*[kmg]bps', rate, re.I):
                raise ValueError('Скорость Hysteria 2 указывается, например, как 50 mbps')
            hy[key] = rate
    if hop:
        interval = params.get('hopInterval', '30').removesuffix('s')
        if not interval.isdigit() or not 5 <= int(interval) <= 3600:
            raise ValueError('Интервал смены порта Hysteria 2: от 5 до 3600 секунд')
        hy['udphop'] = {'port': hop, 'interval': int(interval)}
    stream = {'network': 'hysteria', 'security': 'tls', 'tlsSettings': tls, 'hysteriaSettings': hy}
    if params.get('obfs') == 'salamander':
        stream['finalmask'] = {'udp': [{'type': 'salamander', 'settings': {'password': obfs}}]}
    outbound = {'protocol': 'hysteria', 'settings': {'version': 2, 'address': host, 'port': port}, 'streamSettings': stream}
    return migrate_config({'outbounds': [outbound]})['outbounds'][0]


def server_node(outbound):
    settings = outbound.get('settings', {})
    return settings if outbound.get('protocol') == 'hysteria' else (settings.get('vnext') or [{}])[0]


def configured(outbound):
    node = server_node(outbound)
    address, port = node.get('address'), node.get('port')
    if not isinstance(address, str) or not address or address.startswith('@') or not isinstance(port, int) or not 1 <= port <= 65535:
        return False
    stream = outbound.get('streamSettings', {})
    if str(stream.get('network', '')).startswith('@'):
        return False
    if outbound.get('protocol') == 'hysteria':
        hy = stream.get('hysteriaSettings', {})
        return node.get('version') == 2 and hy.get('version') == 2 and isinstance(hy.get('auth', ''), str)
    return outbound.get('protocol') == 'vless' and bool(node.get('users')) and all(u.get('id') and not u['id'].startswith('@') for u in node['users'])


def quic_certificate(outbound):
    import subprocess
    node = server_node(outbound)
    stream = outbound['streamSettings']
    req = {'address': node['address'], 'port': node['port'], 'sni': stream['tlsSettings']['serverName']}
    masks = stream.get('finalmask', {}).get('udp', [])
    if masks:
        req['obfs_password'] = masks[0]['settings']['password']
    try:
        proc = subprocess.run(['/opt/sbin/pivas-quic-probe'], input=json.dumps(req), text=True,
                              capture_output=True, timeout=8)
        data = json.loads(proc.stdout) if proc.returncode == 0 else {}
        if data.get('status') in ('trusted', 'private', 'invalid'):
            data['pin'] = normalize_pins(data['pin'])
            return data
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
        pass
    return {'status': 'error'}


def normalize_pins(value):
    hashes = [part.strip().replace(':', '').lower() for part in value.split(',')]
    if not hashes or len(hashes) > 8 or any(not re.fullmatch('[0-9a-f]{64}', part) for part in hashes):
        raise ValueError('Отпечаток TLS-сертификата должен быть SHA-256 (64 hex-символа)')
    return ','.join(dict.fromkeys(hashes))


def tls_request(link):
    params = dict(parse_qsl(urlsplit(link).query, keep_blank_values=True))
    flags = [boolean(params[key], key) for key in ('allowInsecure', 'insecure') if key in params]
    if len(set(flags)) > 1:
        raise ValueError('Параметры allowInsecure и insecure противоречат друг другу')
    return flags[0] if flags else None


def certificate_hash(address, port, sni, verified=True, alpn=None):
    """Only a TLS handshake: never send VLESS credentials to a first-seen peer."""
    import hashlib
    import socket
    import ssl
    ca = Path('/opt/etc/ssl/certs/ca-certificates.crt')
    if verified:
        context = ssl.create_default_context(cafile=str(ca) if ca.is_file() else None)
    else:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    context.set_alpn_protocols(alpn or ['h2'])
    with socket.create_connection((address, port), timeout=6) as connection:
        with context.wrap_socket(connection, server_hostname=sni) as tls:
            der = tls.getpeercert(binary_form=True)
            if not der:
                raise ValueError('Сервер не прислал TLS-сертификат')
            return hashlib.sha256(der).hexdigest()


def prepare(link, current=None):
    """Use CA validation, supplied pin or first-use pin for a private certificate.

    A remembered pin is reused, never silently refreshed. Explicit insecure=0
    retains strict CA validation. Legacy insecure=1 requests a pin, not a bypass.
    """
    outbound = parse(link)
    stream = outbound['streamSettings']
    if stream['security'] != 'tls':
        return outbound
    settings = stream['tlsSettings']
    if settings.get('pinnedPeerCertSha256'):
        return outbound
    policy = tls_request(link)
    if policy is False:
        return outbound
    node = server_node(outbound)
    sni = settings['serverName']
    for saved in (current or {}).get('outbounds', []):
        saved_node = server_node(saved)
        saved_tls = saved.get('streamSettings', {}).get('tlsSettings', {})
        if saved.get('protocol') == outbound.get('protocol') and (saved_node.get('address', '').lower(), saved_node.get('port'), saved_tls.get('serverName', '').lower()) == (node['address'].lower(), node['port'], sni.lower()):
            if saved_tls.get('pinnedPeerCertSha256'):
                settings['pinnedPeerCertSha256'] = normalize_pins(saved_tls['pinnedPeerCertSha256'])
                return outbound
    if outbound['protocol'] == 'hysteria' or (stream['network'] == 'xhttp' and settings.get('alpn') == ['h3']):
        cert = quic_certificate(outbound)
        if cert['status'] == 'trusted' and policy is not True:
            return outbound
        if cert['status'] in ('private', 'trusted') or (cert['status'] == 'invalid' and policy is True):
            settings['pinnedPeerCertSha256'] = cert['pin']
            return outbound
        if cert['status'] == 'invalid':
            raise ValueError('QUIC TLS-сертификат просрочен или имеет неверное имя')
        if policy is True:
            raise ValueError('Не удалось получить отпечаток QUIC-сертификата; настройки слота не изменены')
        return outbound  # Offline import retains strict CA verification.
    import ssl
    probe_options = {'alpn': settings.get('alpn', ['h2'])} if stream['network'] == 'xhttp' else {}
    if policy is not True:
        try:
            certificate_hash(node['address'], node['port'], sni, **probe_options)
            return outbound
        except ssl.SSLCertVerificationError as error:
            if error.verify_code not in (2, 18, 19, 20, 21):
                raise ValueError('TLS-сертификат просрочен, имеет неверное имя или не прошёл проверку; автоматическая привязка отменена') from None
        except (OSError, ssl.SSLError):
            # Offline import stays possible with CA verification still enabled.
            return outbound
    try:
        settings['pinnedPeerCertSha256'] = certificate_hash(node['address'], node['port'], sni, verified=False, **probe_options)
    except (OSError, ssl.SSLError):
        raise ValueError('Не удалось получить отпечаток TLS-сертификата; настройки слота не изменены') from None
    return outbound


def replace_slot(config, outbound, slot):
    """Replace connection settings without retaining another transport's fields."""
    result = migrate_config(config)
    tag = "slot-" + str(slot)
    target = next((o for o in result["outbounds"] if o.get("tag") == tag), None)
    if target is None:
        raise ValueError("Не найден указанный VLESS-слот")
    sockopt = target.get("streamSettings", {}).get("sockopt")
    target.update(copy.deepcopy(outbound))
    if sockopt is not None:
        target["streamSettings"]["sockopt"] = sockopt
    if outbound["streamSettings"]["network"] in ("grpc", "hysteria", "xhttp", "splithttp"):
        target["mux"] = {"enabled": False}
    return result


def security_of(stream):
    return stream.get("security") or ("tls" if "tlsSettings" in stream else "reality")


def endpoint(outbound):
    node = server_node(outbound)
    address = node.get("address", "")
    if not address or address.startswith("@"):
        return ""
    host = "[" + address + "]" if ":" in address else address
    return host + ":" + str(node.get("port", ""))


def serialize(outbound):
    server = endpoint(outbound)
    if not server:
        return ""
    if outbound.get('protocol') == 'hysteria':
        outbound = migrate_config({'outbounds': [outbound]})['outbounds'][0]
        stream = outbound.get('streamSettings', {})
        hy, tls = stream.get('hysteriaSettings', {}), stream.get('tlsSettings', {})
        quic = stream.get('finalmask', {}).get('quicParams', {})
        params = {'sni': tls.get('serverName', '')}
        for key in ('up', 'down'):
            if quic.get('brutal' + key.title()):
                params[key] = quic['brutal' + key.title()]
        if quic.get('udpHop'):
            params.update(mport=quic['udpHop']['ports'], hopInterval=str(quic['udpHop']['interval']))
        if 'allowInsecure' in tls:
            params['insecure'] = '1' if tls['allowInsecure'] else '0'
        if tls.get('pinnedPeerCertSha256'):
            params['pinSHA256'] = tls['pinnedPeerCertSha256']
        masks = stream.get('finalmask', {}).get('udp', [])
        if masks:
            params.update(obfs='salamander', **{'obfs-password': masks[0]['settings']['password']})
        credentials = quote(hy.get('auth', ''), safe='') + '@' if hy.get('auth') else ''
        return 'hysteria2://' + credentials + server + '/?' + urlencode(params, quote_via=quote) + '#' + quote(outbound.get('tag', ''), safe='-')
    node = outbound["settings"]["vnext"][0]
    user = node["users"][0]
    stream = outbound.get("streamSettings", {})
    security = security_of(stream)
    params = {"type": stream.get("network", "tcp"), "security": security}
    if security == "reality":
        reality = stream.get("realitySettings", {})
        params.update(pbk=reality.get("publicKey", reality.get("password", "")),
                      fp=reality.get("fingerprint", ""), sni=reality.get("serverName", ""),
                      sid=reality.get("shortId", ""), spx=reality.get("spiderX", "/"), flow=user.get("flow", ""))
    elif security == "tls":
        tls, grpc = stream.get("tlsSettings", {}), stream.get("grpcSettings", {})
        params.update(sni=tls.get("serverName", ""))
        if params['type'] == 'grpc':
            params.update(serviceName=grpc.get("serviceName", ""), mode="multi" if grpc.get("multiMode") else "gun")
        for key, value in (("fp", tls.get("fingerprint")), ("authority", grpc.get("authority"))):
            if value:
                params[key] = value
        if tls.get("alpn"):
            params["alpn"] = ",".join(tls["alpn"])
        if "allowInsecure" in tls:
            params["allowInsecure"] = "1" if tls["allowInsecure"] else "0"
        if tls.get("pinnedPeerCertSha256"):
            params["pcs"] = tls["pinnedPeerCertSha256"]
        if tls.get("verifyPeerCertByName"):
            params["vcn"] = tls["verifyPeerCertByName"]
    if params['type'] in ('xhttp', 'splithttp'):
        params['type'] = 'xhttp'
        xhttp = stream.get('xhttpSettings', stream.get('splithttpSettings', {}))
        params.update(path=xhttp.get('path', '/'), host=xhttp.get('host', ''), mode=xhttp.get('mode') or 'auto')
        if xhttp.get('extra'):
            params['extra'] = json.dumps(xhttp['extra'], ensure_ascii=False, separators=(',', ':'))
    return "vless://" + quote(user.get("id", ""), safe="-") + "@" + server + "?" + urlencode(params, quote_via=quote) + "#" + quote(outbound.get("tag", ""), safe="-")


def state(config):
    outbounds = {o.get("tag"): o for o in config.get("outbounds", [])}
    result = {}
    for number in (1, 2):
        out = outbounds.get("slot-" + str(number), {})
        stream = out.get("streamSettings", {})
        result["slot" + str(number)] = {
            "server": endpoint(out), "url": serialize(out),
            "transport": ("HYSTERIA 2 / QUIC" if out.get('protocol') == 'hysteria' else stream.get("network", "tcp").upper() + " / " + security_of(stream).upper()) if endpoint(out) else "",
        }
    first, second = (outbounds.get("slot-" + str(n), {}) for n in (1, 2))
    result["slot2_same"] = bool(result["slot1"]["server"] and result["slot2"]["server"] and
                               {k: v for k, v in first.items() if k != "tag"} ==
                               {k: v for k, v in second.items() if k != "tag"})
    return result


def state_shell(config):
    values = state(config)
    lines = []
    for number in (1, 2):
        summary = values["slot" + str(number)]
        for suffix, key in (("srv", "server"), ("url", "url"), ("transport", "transport")):
            lines.append("slot%s_%s=%s" % (number, suffix, shlex.quote(summary[key])))
    lines.append("slot2_same=" + str(values["slot2_same"]).lower())
    return "\n".join(lines)


def show(config, slot):
    out = next((o for o in config.get("outbounds", []) if o.get("tag") == "slot-" + str(slot)), {})
    print("Slot %s:" % slot)
    if not endpoint(out):
        print("  (не настроен — выполните: pivas vless set-%s <vless://...>)" % slot)
        return
    if out.get('protocol') == 'hysteria':
        stream = out.get('streamSettings', {})
        tls = stream.get('tlsSettings', {})
        print('  Сервер: ' + endpoint(out))
        print('  Transport: Hysteria 2 / QUIC / TLS')
        print('  SNI: ' + tls.get('serverName', ''))
        print('  TLS SHA-256: ' + tls.get('pinnedPeerCertSha256', 'проверка CA'))
        print('  URL: ' + serialize(out))
        return
    user = out["settings"]["vnext"][0]["users"][0]
    stream = out.get("streamSettings", {})
    settings = stream.get("realitySettings", {}) if security_of(stream) == "reality" else stream.get("tlsSettings", {})
    fields = [("Сервер:", endpoint(out)), ("UUID:", user.get("id", "")),
              ("Transport:", stream.get("network", "tcp")), ("Security:", security_of(stream)),
              ("Flow:", user.get("flow", "")), ("Fingerprint:", settings.get("fingerprint", "")),
              ("SNI:", settings.get("serverName", ""))]
    if security_of(stream) == "reality":
        fields += [("ShortID:", settings.get("shortId", "")), ("PublicKey:", settings.get("publicKey", settings.get("password", "")))]
    if stream.get('network') in ('xhttp', 'splithttp'):
        xhttp = stream.get('xhttpSettings', stream.get('splithttpSettings', {}))
        fields += [('Path:', xhttp.get('path', '/')), ('Host:', xhttp.get('host', '')), ('Mode:', xhttp.get('mode') or 'auto')]
        if security_of(stream) == 'tls':
            fields += [('ALPN:', ','.join(settings.get('alpn', []))), ('TLS SHA-256:', settings.get('pinnedPeerCertSha256', 'проверка CA'))]
    elif security_of(stream) == 'tls':
        grpc = stream.get("grpcSettings", {})
        fields += [("ServiceName:", grpc.get("serviceName", "")), ("Mode:", "multi" if grpc.get("multiMode") else "gun"),
                   ("Authority:", grpc.get("authority", "")), ("ALPN:", ",".join(settings.get("alpn", []))),
                   ("TLS SHA-256:", settings.get("pinnedPeerCertSha256", "проверка CA"))]
    for label, value in fields:
        print("  %-14s %s" % (label, value))
    print("  URL: " + serialize(out))


def main():
    try:
        action = sys.argv[1]
        if action == "parse":
            print(json.dumps(parse(sys.stdin.read().strip()), ensure_ascii=False))
        elif action == "prepare":
            current = {}
            config = Path(sys.argv[2])
            if config.is_file():
                try:
                    current = json.loads(config.read_text())
                except ValueError:
                    pass  # Existing legacy configs are initialized by the shell.
            print(json.dumps(prepare(sys.stdin.read().strip(), current), ensure_ascii=False))
        elif action == "replace":
            config = json.loads(Path(sys.argv[2]).read_text())
            print(json.dumps(replace_slot(config, json.loads(sys.stdin.read()), int(sys.argv[3])), indent=2, ensure_ascii=False))
        elif action == "state-shell":
            print(state_shell(json.loads(Path(sys.argv[2]).read_text())))
        elif action == "show":
            show(json.loads(Path(sys.argv[2]).read_text()), int(sys.argv[3]))
        elif action == 'configured':
            config = json.loads(Path(sys.argv[2]).read_text())
            out = next((o for o in config.get('outbounds', []) if o.get('tag') == 'slot-' + sys.argv[3]), {})
            return 0 if configured(out) else 1
        elif action == 'migrate':
            print('migrated' if migrate_file(sys.argv[2]) else 'unchanged')
        else:
            raise ValueError("Неизвестная команда VLESS")
    except RuntimeError:
        print('Конфигурация Xray не прошла проверку; изменения отменены', file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
        # JSON decode/path errors can include data; report only our own values.
        message = str(error) if type(error) is ValueError else "Не удалось прочитать конфигурацию VLESS"
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
