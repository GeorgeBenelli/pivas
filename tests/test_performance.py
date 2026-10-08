import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import performance

class PerformanceTests(unittest.TestCase):
    def test_sample_cpu_memory_and_process_counters(self):
        first=(1000,700,{'3':dict(name='xray',ticks=100,rss_kib=2000,start='1')},{'MemTotal':10000,'MemAvailable':8000})
        second=(1200,800,{'3':dict(name='xray',ticks=110,rss_kib=2400,start='1')},{'MemTotal':10000,'MemAvailable':7000})
        with patch.object(performance,'counters',side_effect=[first,second]),patch.object(performance.time,'sleep'),patch.object(performance.time,'monotonic',side_effect=[10,12]),patch.object(performance.os,'sysconf',return_value=100):
            r=performance.sample()
        self.assertEqual(r['cpu_percent'],50);self.assertEqual(r['processes'][0]['cpu_one_core_percent'],5);self.assertEqual(r['memory_kib']['MemAvailable'],7000)
    def test_missing_proc_is_explicit_not_zero_load(self):
        with tempfile.TemporaryDirectory() as temp:r=performance.sample(Path(temp))
        self.assertFalse(r['available']);self.assertNotIn('cpu_percent',r)
    def test_process_names_never_expose_command_line_secrets(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);proc=root/'proc';(proc/'3').mkdir(parents=True)
            (proc/'stat').write_text('cpu 100 0 20 80 0 0 0 0\n');(proc/'meminfo').write_text('MemTotal: 10000 kB\nMemAvailable: 7000 kB\n')
            fields=['S']+['0']*21;fields[11]='100';fields[12]='10';fields[19]='123';fields[21]='10'
            (proc/'3/stat').write_text('3 (python3) '+' '.join(fields));(proc/'3/cmdline').write_bytes(b'python3\0/opt/etc/telegram4pivas/telegram_bot.py\0FAKE_SECRET')
            counters=performance.counters(root)
            self.assertEqual(counters[2]['3']['name'],'Pivas: бот');self.assertNotIn('FAKE_SECRET',json.dumps(counters))
if __name__=='__main__':unittest.main()
