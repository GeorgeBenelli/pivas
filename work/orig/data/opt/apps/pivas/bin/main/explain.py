"""Explain configured policy without fetching the requested site or its secrets."""
import ipaddress
import catalog
import devices

def handle(rt,request):
    value=request.get('domain','')
    try:address=ipaddress.ip_address(value.strip());domain=str(address)
    except ValueError:address=None;domain=catalog.normalize(value)
    chosen=request.get('mac','');excluded=bool(chosen and devices.mac(chosen) in devices.load(rt))
    main,second=rt.entries(catalog.MAIN),rt.entries(catalog.SECOND)
    if address:
        matches=[v for v in main|second if '/' in v and address.version==ipaddress.ip_network(v).version and address in ipaddress.ip_network(v)]
        matches.sort(key=lambda v:ipaddress.ip_network(v).prefixlen,reverse=True)
    else:
        matches=sorted((v for v in rt.domains(main|second) if domain==v or domain.endswith('.'+v)),key=lambda v:len(v),reverse=True)
    rule=matches[0] if matches else None;slot=(2 if rule in second else 1) if rule else None
    group=next((g['name'] for g in catalog.load(rt) if g['enabled'] and rule in g['domains']),None)
    paused=rt.path('/opt/etc/pivas.paused').exists()
    cfg=rt.config();reason='Применено наиболее точное активное правило' if rule else 'Домен отсутствует в активных списках'
    dns=' → '.join(rt.upstreams(cfg));route='Слот '+str(slot) if slot else 'Обычный маршрут Keenetic'
    if slot:
        dns='DNSCrypt слота '+str(slot)
        if rt.path('/opt/tmp/pivas-slot%s-dns.down'%slot).exists():
            if slot==2 and cfg.get('DNS_SLOT2_FALLBACK')=='slot1' and not rt.path('/opt/tmp/pivas-slot1-dns.down').exists():dns='DNSCrypt слота 1 (резерв)'
            elif cfg.get('DNS_VPN_FAILURE','closed')=='closed':dns='Ответ DNS заблокирован политикой отказа'
            else:dns=' → '.join(rt.upstreams(cfg))+' (резерв)'
    if excluded or paused:
        route='Обычный маршрут Keenetic';dns='DNS устройства / штатный DNS Keenetic'
        reason='Устройство исключено из Pivas' if excluded else 'Pivas на паузе'
    elif rt.path('/opt/tmp/pivas-dns-watchdog.bypass').exists():
        dns='DNS устройства / штатный DNS Keenetic (аварийно снят перехват)'
    if address and address.version==6:
        route='IPv6: маршрут определяет Keenetic';slot=None
        reason='Pivas управляет IPv4; отдельной политики IPv6 здесь нет'
    if address:dns='Для IP-адреса DNS не используется'
    return dict(domain=domain,rule=rule,group=group,slot=slot,route=route,dns=dns,reason=reason,excluded=excluded,paused=paused,
                note='Это расчёт по настройкам. Старые соединения, общие IP CDN, IPv6 и собственный DNS приложения могут влиять на фактический путь. Для IP проверяются явные IP/CIDR-правила, а не все адреса, выученные из DNS.')
