"""Password-encrypted, authenticated Pivas settings; no archive extraction."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
import catalog
import devices
import slots as slot_names

MAX=4*1024*1024
DNS_KEYS=('DNS_PROVIDER','DNS_VPN_FAILURE','DNS_SLOT2_FALLBACK','DNS_STAMP_1','DNS_STAMP_2')
FORMAT='pivas-backup-1'

def b64(data):return base64.b64encode(data).decode()
def un64(data):return base64.b64decode(data,validate=True)
def password(value):
    if not isinstance(value,str) or not 12<=len(value)<=256:raise ValueError('Пароль копии: от 12 до 256 символов')
    return value.encode()
def revision(rt):
    h=hashlib.sha256()
    for n in (catalog.META,catalog.MAIN,catalog.SECOND,devices.CONFIG,slot_names.CONFIG,rt.XRAY,'/opt/etc/pivas.conf'):
        h.update(n.encode()+b'\0'+(rt.path(n).read_bytes() if rt.path(n).exists() else b'')+b'\0')
    return h.hexdigest()
def crypt(data,key,decrypt=False):
    read,write=os.pipe()
    try:
        os.write(write,base64.b64encode(key)+b'\n');os.close(write);write=None
        result=subprocess.run(['openssl','enc','-aes-256-cbc','-pbkdf2','-iter','100000','-md','sha256','-pass','fd:'+str(read)]+(['-d'] if decrypt else []),input=data,capture_output=True,pass_fds=(read,),timeout=30)
        if result.returncode:raise ValueError('Не удалось обработать копию: проверьте openssl-util и пароль')
        return result.stdout
    finally:
        os.close(read)
        if write is not None:os.close(write)
def seal(data,phrase):
    raw=json.dumps(data,ensure_ascii=False).encode()
    if len(raw)>MAX:raise ValueError('Копия превышает 4 МБ')
    salt=secrets.token_bytes(16);keys=hashlib.pbkdf2_hmac('sha256',password(phrase),salt,200000,64)
    cipher=crypt(raw,keys[:32]);tag=hmac.new(keys[32:],FORMAT.encode()+salt+cipher,'sha256').digest()
    return dict(format=FORMAT,salt=b64(salt),ciphertext=b64(cipher),mac=b64(tag))
def unseal(envelope,phrase):
    try:
        if not isinstance(envelope,dict) or envelope.get('format')!=FORMAT:raise ValueError()
        if len(envelope.get('ciphertext',''))>(MAX+128)*4//3+4:raise ValueError()
        salt,cipher,tag=[un64(envelope[k]) for k in ('salt','ciphertext','mac')]
        if len(salt)!=16 or len(tag)!=32:raise ValueError()
        keys=hashlib.pbkdf2_hmac('sha256',password(phrase),salt,200000,64)
        if not hmac.compare_digest(tag,hmac.new(keys[32:],FORMAT.encode()+salt+cipher,'sha256').digest()):raise ValueError()
        raw=crypt(cipher,keys[:32],True)
        if len(raw)>MAX:raise ValueError()
        return json.loads(raw)
    except (ValueError,KeyError,TypeError):raise ValueError('Неверный пароль или повреждённая копия') from None

def export(rt):
    state=catalog.view(rt)
    if state['drift']:raise ValueError('Сначала устраните расхождения групп и списков')
    config=json.loads(rt.path(rt.XRAY).read_text()) if rt.path(rt.XRAY).exists() else {'outbounds':[]}
    return dict(version=1,groups=state['groups'],main=sorted(rt.entries(catalog.MAIN)),second=sorted(rt.entries(catalog.SECOND)),devices=devices.load(rt),slot_names=slot_names.load(rt),slots=[o for o in config['outbounds'] if o.get('tag') in ('slot-1','slot-2')],dns={k:v for k,v in rt.config().items() if k in DNS_KEYS})

def validate(rt,data):
    if not isinstance(data,dict) or data.get('version')!=1:raise ValueError('Неизвестный формат настроек')
    required=set(('version','groups','main','second','devices','slots','dns'))
    if not required<=set(data) or set(data)-required-{'slot_names'}:raise ValueError('Неизвестные поля копии')
    names=slot_names.validate(data.get('slot_names',slot_names.DEFAULTS))
    if not isinstance(data['main'],list) or not isinstance(data['second'],list) or len(data['main'])>10000:raise ValueError('Некорректные списки')
    if any(not isinstance(v,str) or '\n' in v or '\r' in v for v in data['main']+data['second']):raise ValueError('Некорректная запись списка')
    main,second=[rt.parse_entries('\n'.join(data[k])) for k in ('main','second')]
    if not second<=main:raise ValueError('Второй список должен входить в общий')
    if not isinstance(data['groups'],list) or len(data['groups'])>200:raise ValueError('Не более 200 групп')
    if not isinstance(data['devices'],dict) or len(data['devices'])>devices.MAX_EXCLUDED:raise ValueError('Не более 10 исключённых устройств')
    excluded={devices.mac(k):v for k,v in data['devices'].items()}
    if any(not isinstance(v,str) or len(v)>120 for v in excluded.values()):raise ValueError('Некорректное имя устройства')
    if not isinstance(data['dns'],dict) or set(data['dns'])-set(DNS_KEYS) or any(not isinstance(v,str) or any(c in v for c in '\r\n\0') for v in data['dns'].values()):raise ValueError('Некорректные настройки DNS')
    cfg=rt.config();defaults={'DNS_VPN_FAILURE':'closed','DNS_SLOT2_FALLBACK':'policy'};cfg.update({k:data['dns'].get(k,defaults.get(k,'')) for k in DNS_KEYS})
    rt.upstreams(cfg);rt.servers(cfg,main,second);rt.dnscrypt(cfg,1);rt.dnscrypt(cfg,2)
    files={catalog.META:json.dumps({'version':1,'groups':data['groups']}).encode(),catalog.MAIN:('\n'.join(sorted(main))+'\n').encode(),catalog.SECOND:('\n'.join(sorted(second))+'\n').encode(),devices.CONFIG:json.dumps({'version':1,'excluded':excluded}).encode()}
    files[slot_names.CONFIG]=slot_names.encode(names)
    with tempfile.TemporaryDirectory(prefix='pivas-restore-',dir=rt.path('/opt/tmp')) as temp:
        def path(n):return Path(temp)/n.lstrip('/')
        for n,raw in files.items():path(n).parent.mkdir(parents=True,exist_ok=True);path(n).write_bytes(raw)
        shadow=SimpleNamespace(path=path,entries=lambda n:rt.parse_entries(path(n).read_text()))
        state=catalog.view(shadow)
        if len(state['standalone'])+sum(len(g['domains']) for g in state['groups'])>10000:raise ValueError('Не более 10000 доменов')
        if state['drift']:raise ValueError('Группы и списки в копии не согласованы')
    slots=data['slots']
    if not isinstance(slots,list) or len(slots)!=2 or any(not isinstance(o,dict) for o in slots) or {o.get('tag') for o in slots}!={'slot-1','slot-2'} or any(o.get('protocol') not in ('vless','hysteria') for o in slots):raise ValueError('В копии нужны два слота VLESS или Hysteria 2')
    source=rt.path(rt.XRAY) if rt.path(rt.XRAY).exists() else rt.path('/opt/apps/pivas/etc/conf/pivas.vless')
    xray=json.loads(source.read_text());xray['outbounds']=[o for o in xray['outbounds'] if o.get('tag') not in ('slot-1','slot-2')]+slots
    candidate=rt.routing(xray,main,second);rt.validate_xray(json.dumps(candidate).encode())
    lines=[line for line in rt.path('/opt/etc/pivas.conf').read_text().splitlines() if line.partition('=')[0].strip() not in DNS_KEYS]
    files['/opt/etc/pivas.conf']=('\n'.join(lines+[k+'='+cfg[k] for k in DNS_KEYS])+'\n').encode()
    return files,main,second,cfg,xray,excluded

def handle(rt,request):
    action=request.get('action');phrase=request.get('password');password(phrase)
    if action=='export':return {'archive':seal(export(rt),phrase)}
    data=unseal(request.get('archive'),phrase)
    files,main,second,cfg,xray,excluded=validate(rt,data)
    summary=dict(groups=len(data['groups']),domains=len(main),excluded=len(excluded),slots=2)
    if action=='preview':return {'summary':summary,'revision':revision(rt)}
    if action!='restore' or request.get('revision')!=revision(rt):raise ValueError('Настройки изменились. Проверьте копию ещё раз')
    previous=devices.load(rt)
    rt.atomic(rt.path('/opt/etc/pivas-backup/before-restore.pivas'),json.dumps(seal(export(rt),phrase)).encode())
    rt.apply(list_values=(main,second),extra_changes=files,config_values=cfg,xray_value=xray,activate_extra=lambda:devices.firewall(rt,excluded),rollback_extra=lambda:devices.firewall(rt,previous))
    rt.path('/opt/tmp/pivas-web-state.json').unlink(missing_ok=True)
    return {'restored':True,'summary':summary}
