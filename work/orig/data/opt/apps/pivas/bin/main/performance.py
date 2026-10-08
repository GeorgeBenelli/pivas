"""On-demand bounded /proc samples; no background collector or traffic generation."""
import json
import os
from pathlib import Path
import subprocess
import time

NAMES={'xray','dnsmasq','dnscrypt-proxy','dnscrypt-slot2','python3','python'}

def counters(root):
    fields=(root/'proc/stat').read_text().splitlines()[0].split()[1:9]
    cpu=list(map(int,fields));processes={}
    for path in (root/'proc').iterdir():
        if not path.name.isdigit():continue
        try:
            text=(path/'stat').read_text();end=text.rindex(')');name=text[text.index('(')+1:end]
            if name not in NAMES and not name.startswith("python"):continue
            if name.startswith("python"):
                command=(path/"cmdline").read_bytes()[:512]
                for needle,label in ((b"telegram_bot.py","Pivas: бот"),(b"pivas-web/server.py","Pivas: веб"),(b"runtime.py","Pivas: применение"),(b"diagnostics.py","Pivas: диагностика")):
                    if needle in command:name=label;break
            fields=text[end+2:].split();ticks=int(fields[11])+int(fields[12]);rss=int(fields[21])*os.sysconf('SC_PAGE_SIZE')//1024
            processes[path.name]=dict(name=name,ticks=ticks,rss_kib=rss,start=fields[19])
        except (OSError,ValueError,IndexError):pass
    memory={}
    for line in (root/'proc/meminfo').read_text().splitlines():
        key,_,value=line.partition(':')
        if key in ('MemTotal','MemAvailable','MemFree','SwapTotal','SwapFree'):memory[key]=int(value.split()[0])
    return sum(cpu),cpu[3]+(cpu[4] if len(cpu)>4 else 0),processes,memory

def sample(root=Path('/'),seconds=2):
    try:
        before=counters(root);started=time.monotonic();time.sleep(seconds);after=counters(root)
    except (OSError,ValueError,IndexError):return {'available':False,'note':'Счётчики /proc недоступны'}
    elapsed=time.monotonic()-started;total=max(1,after[0]-before[0]);rows=[]
    hz=os.sysconf('SC_CLK_TCK')
    for pid,p in after[2].items():
        old=before[2].get(pid)
        pct=round(100*max(0,p['ticks']-old['ticks'])/(hz*elapsed),1) if old and old['start']==p['start'] else None
        rows.append(dict(pid=int(pid),name=p['name'],rss_kib=p['rss_kib'],cpu_one_core_percent=pct))
    return dict(available=True,seconds=round(elapsed,2),cpu_percent=round(100*(1-(after[1]-before[1])/total),1),memory_kib=after[3],processes=sorted(rows,key=lambda p:p['rss_kib'],reverse=True),note='CPU роутера — доля общей мощности; CPU процесса — доля одного ядра. RSS процессов нельзя складывать как точный расход общей памяти.')

def handle(rt,request):
    label=request.get('label','idle')
    if label not in ('idle','load'):raise ValueError('Выберите покой или нагрузку')
    result=sample(rt.BASE);result['label']=label;result['dns']=[]
    for port in (53,9753,9153,9154):
        start=time.monotonic()
        try:
            p=rt.command('dig','+time=1','+tries=1','+noall','+comments','+stats','@127.0.0.1','-p',str(port),'example.com','A',check=False,timeout=2)
            result['dns'].append(dict(port=port,ms=round((time.monotonic()-start)*1000),ok=p.returncode==0 and 'status: NOERROR' in p.stdout))
        except (OSError,RuntimeError,subprocess.TimeoutExpired):result['dns'].append(dict(port=port,ok=False))
    result['note']=result.get('note','')+' DNS-пробы могут использовать кэш; трафик для нагрузочного теста автоматически не создаётся.'
    return result
