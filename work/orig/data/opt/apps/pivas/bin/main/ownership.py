"""Per-rule learned IPv4 ownership. Packet matching still uses one aggregate set.

Caller holds runtime.lock. Removal runs while dnsmasq is stopped, so a newly
learned shared address cannot race the ownership snapshot and deletion.
"""
import hashlib
import ipaddress
import json
import shlex
import time

STATE='/opt/etc/pivas-ip-owners.json'
RETIRED='/opt/etc/pivas-retired-domains.json'
IPSET='/opt/sbin/ipset'
AGG='PIVAS_LIST'
LEGACY='PIVAS_LEGACY'

def name(domain):return 'PVO_'+hashlib.sha256(domain.encode()).hexdigest()[:20]

def load(rt):
    return json.loads(rt.path(STATE).read_text()) if rt.path(STATE).exists() else {'domains':sorted(rt.domains(rt.entries('/opt/etc/pivas.list'))), 'networks':sorted(v for v in rt.entries('/opt/etc/pivas.list') if '/' in v)}

def snapshot(rt):
    sets={}
    for line in rt.command(IPSET,'save').stdout.splitlines():
        a=shlex.split(line)
        if len(a)<3 or not (a[1] in (AGG,LEGACY) or a[1].startswith('PVO_')):continue
        if a[0]=='create':sets.setdefault(a[1],{})
        if a[0]=='add':
            network=ipaddress.ip_network(a[2],strict=False)
            if network.version!=4:continue
            ttl=int(a[a.index('timeout')+1]) if 'timeout' in a else 0
            sets.setdefault(a[1],{})[str(network)]=ttl
    return sets

def prepare(rt,values):
    desired=rt.domains(values);old=load(rt);existing=set(rt.command(IPSET,'list','-name').stdout.split());sets=snapshot(rt) if LEGACY not in existing and AGG in existing else {}
    ttl=int(rt.config().get('LIST_IPSET_TTL','86400'))
    if not 60<=ttl<=604800:raise ValueError('LIST_IPSET_TTL: от 60 до 604800 секунд')
    lines=[]
    for key,kind in [(AGG,'hash:net'),(LEGACY,'hash:net')]+[(name(d),'hash:ip') for d in sorted(desired)]:
        if key not in existing:
            lines.append('create %s %s family inet hashsize 64 maxelem 65536 timeout %s' %(key,kind,ttl))
    if LEGACY not in existing:
        # Existing aggregate entries have no historical domain attribution.
        # Preserve them only for their remaining lifetime during migration.
        lines += ['add %s %s timeout %s'%(LEGACY,ip,max(1,t or ttl)) for ip,t in sets.get(AGG,{}).items()]
    created=[line.split()[1] for line in lines if line.startswith('create ')]
    limit=rt.path('/sys/module/ip_set/parameters/max_sets')
    if limit.exists():
        maximum=int(limit.read_text()) or 256
        # A zero module parameter means the kernel's compiled CONFIG_IP_SET_MAX,
        # not unlimited. Its upstream default is 256; this is a conservative
        # preflight when the router does not expose its compiled config.
        if len(existing)+len(created)+1>maximum:
            raise ValueError('Недостаточно ipset для учёта доменов: лимит ядра %s; уменьшите число активных доменов'%maximum)
    if lines:
        try:rt.command(IPSET,'restore','-exist',input_text='\n'.join(lines)+'\n')
        except BaseException:
            for key in reversed(created):
                try:rt.command(IPSET,'destroy',key,check=False)
                except Exception:pass
            raise
    return bool(created)

def retired(rt,values):
    ttl=int(rt.config().get('LIST_IPSET_TTL','86400'))
    if not 60<=ttl<=604800:raise ValueError('LIST_IPSET_TTL: от 60 до 604800 секунд')
    now=int(time.time())
    saved=json.loads(rt.path(RETIRED).read_text()) if rt.path(RETIRED).exists() else {}
    previous={d:now+ttl+300 for d in saved} if isinstance(saved,list) else saved
    if not isinstance(previous,dict):raise ValueError('Некорректный журнал удалённых доменов')
    active=rt.domains(values);old=set(load(rt)['domains'])
    result={d:expiry for d,expiry in previous.items() if type(expiry) is int and expiry>now and not any(d==a or d.endswith('.'+a) for a in active)}
    result.update({d:now+ttl+300 for d in old-active if not any(d==a or d.endswith('.'+a) for a in active)})
    if len(result)>20000:raise ValueError('Слишком много сохранённых правил удаления доменов')
    return dict(sorted(result.items()))

def compile_rules(values):
    return ''.join('ipset=/%s/%s,%s\n'%(d,AGG,name(d)) for d in sorted(v for v in values if '/' not in v))

def reconcile(rt,old,values):
    active=rt.domains(values);nets={str(ipaddress.ip_network(n)) for n in values if '/' in n and ipaddress.ip_network(n).version==4}
    sets=snapshot(rt);aggregate=sets.get(AGG,{})
    protected=set(sets.get(LEGACY,{}));transfers=[];candidates={};active_names={name(d) for d in active}
    for d in active:protected.update(sets.get(name(d),{}))
    for d in set(old['domains'])-active:
        entries=sets.get(name(d),{});candidates.update(entries)
        parents=sorted((a for a in active if d.endswith('.'+a)),key=len,reverse=True)
        if parents:
            protected.update(entries)
            transfers.extend('add %s %s timeout %s'%(name(parents[0]),ip,t or 86400) for ip,t in entries.items())
        if not parents and any(a.endswith('.'+d) for a in active):
            # Old suffix-wide observations cannot be assigned to the surviving child.
            # Keep them for their remaining TTL rather than break its cached addresses.
            protected.update(entries)
            transfers.extend('add %s %s timeout %s'%(LEGACY,ip,t or 86400) for ip,t in entries.items())
    oldnets=set(old.get('networks',[]));candidates.update({n:aggregate[n] for n in oldnets-nets if n in aggregate})
    protected_networks=[ipaddress.ip_network(n) for n in protected|nets]
    # A protected static entry must not become an immortal orphan after its
    # rule is removed. Return it to a bounded learned-address lifetime.
    age_out=[]
    for n,t in candidates.items():
        if t==0 and n not in nets:
            lifetimes=[ips[n] for key,ips in sets.items() if key==LEGACY or key in active_names if n in ips]
            age_out.append('add %s %s timeout %s'%(AGG,n,max(lifetimes or [86400]) or 86400))
    remove={n:t for n,t in candidates.items() if n in aggregate and not any(ipaddress.ip_network(n).subnet_of(p) for p in protected_networks)}
    additions={n for n in nets if aggregate.get(n)!=0}
    lines=transfers+age_out+['del %s %s'%(AGG,n) for n in sorted(remove)]+['add %s %s timeout 0'%(AGG,n) for n in sorted(additions)]
    if lines:
        try:rt.command(IPSET,'restore','-exist',input_text='\n'.join(lines)+'\n')
        except BaseException:
            rollback=['del %s %s'%(AGG,n) for n in additions]+['add %s %s timeout %s'%(AGG,n,t) for n,t in aggregate.items()]
            if rollback:rt.command(IPSET,'restore','-exist',input_text='\n'.join(rollback)+'\n')
            raise
    return {'removed_addresses':len(remove),'shared_or_inherited':len(set(candidates)-set(remove)), 'removed':list(remove)}

def undo(rt,sets):
    # dnsmasq is stopped by the caller; swap restores the aggregate atomically.
    temp='PIVAS_RESTORE_TMP'
    rt.command(IPSET,'destroy',temp,check=False)
    lines=['create '+temp+' hash:net family inet hashsize 64 maxelem 65536 timeout 86400']
    lines += ['add %s %s timeout %s'%(temp,n,t) for n,t in sets.get(AGG,{}).items()]
    lines += ['swap '+temp+' '+AGG,'destroy '+temp]
    rt.command(IPSET,'restore','-exist',input_text='\n'.join(lines)+'\n')

def finish(rt,old,values,result):
    # Only after the transaction and service activation have both succeeded.
    for d in set(old['domains'])-rt.domains(values):
        try:rt.command(IPSET,'destroy',name(d),check=False)
        except Exception:pass
    for n in result.get('removed',[])[:32]:
        network=ipaddress.ip_network(n)
        if network.prefixlen==32:
            try:rt.command('conntrack','-D','-f','ipv4','--orig-dst',str(network.network_address),'--mark','0xd1000/0xd1000',check=False,timeout=1)
            except Exception:pass
