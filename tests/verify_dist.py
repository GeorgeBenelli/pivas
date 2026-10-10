"""Read-only check of shipped bytes, architecture bundles and installer payloads."""
from pathlib import Path
import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]
s = importlib.util.spec_from_file_location('builder', ROOT / 'build.py')
b = importlib.util.module_from_spec(s); s.loader.exec_module(b)
manifest = json.loads((ROOT / 'dist/manifest.json').read_text())
assert len(manifest['sha256']) == 13
for name, expected in manifest['sha256'].items():
    assert hashlib.sha256((ROOT/'dist'/name).read_bytes()).hexdigest() == expected, name

def unpack(p): return b.unpack(p.read_bytes(), 'data')
corep=next((ROOT/'dist/common').glob('pivas_*'))
webp=next((ROOT/'dist/common').glob('pivas-web_*'))
botp=next((ROOT/'dist/common').glob('telegram4pivas_*'))
core,web,bot = [unpack(p) for p in (corep,webp,botp)]
assert core == b.tree(ROOT/'work/orig/data')
core_control = b.unpack(corep.read_bytes(), 'control')
assert b'runtime.py dns-services || exit 1' in core_control['postinst'][0]
for name in ('S56dnsmasq', 'S09dnscrypt-proxy2'):
    assert 'opt/apps/pivas/etc/service-templates/' + name in core
    # These files belong to Entware dependencies, not the Pivas payload.
    assert 'opt/etc/init.d/' + name not in core
assert web == b.tree(ROOT/'web-src/data')
source=b.tree(ROOT/'bot')
expected={'opt/etc/telegram4pivas/'+n:v for n,v in source.items() if n!='README.md'}
expected['opt/etc/init.d/S98telegram4pivas']=source['S98telegram4pivas']
assert bot == expected
for group in (core,web,bot):
    assert not any(n.endswith(('telegram_bot_config.py','.pyc','.log')) for n in group)
    assert not any('/__pycache__/' in n for n in group)
for arch in ('mipsel','aarch64'):
    directory=ROOT/'dist'/arch
    wrapper=next(directory.glob('xray_*'))
    binary=next(directory.glob('xray-core_*'))
    probe=next(directory.glob('pivas-quic-probe_*'))
    expected_probe=(ROOT/'vendor/quic-probe'/('mipsel-3.4' if arch=='mipsel' else 'aarch64-3.10')).read_bytes()
    assert unpack(probe) == {'opt/sbin/pivas-quic-probe':(expected_probe,0o755),
                            'opt/share/pivas-quic-probe/LICENSES.txt':((ROOT/'vendor/quic-probe/LICENSES.txt').read_bytes(),0o644)}
    vendored=ROOT/'vendor/xray'/('mipsel-3.4' if arch=='mipsel' else 'aarch64-3.10')
    assert unpack(binary)['opt/sbin/xray'] == (vendored.read_bytes(),0o755)
    assert b.unpack(binary.read_bytes(),'control')['control'][0].decode().split('Version: ')[1].splitlines()[0] == b.VERSIONS['xray-core']
    provenance=json.loads((ROOT/'vendor/xray/manifest.json').read_text())
    if arch=='mipsel':
        assert provenance['assets']['mipsel-3.4']['origin']=='source-build'
        assert provenance['assets']['mipsel-3.4']['go']=='1.26.6'
    assert b.unpack(wrapper.read_bytes(),'control')['control'][0].decode().split('Version: ')[1].splitlines()[0].startswith('26.3.27-')
    assert unpack(wrapper) == b.tree(ROOT/'xray-src/data')
    full=unpack(next(directory.glob('pivas-full_*')))
    full_control = b.unpack(next(directory.glob('pivas-full_*')).read_bytes(), 'control')
    assert b'runtime.py dns-services || exit 1' in full_control['postinst'][0]
    merged={}
    for group in (unpack(binary),unpack(wrapper),unpack(probe),core,web,bot):merged.update(group)
    assert full == merged
    assert {p.name for p in directory.glob('install-*.sh')} == {'install-pivas-full.sh'}
    for installer in directory.glob('install-*.sh'):
        header,payload=installer.read_bytes().split(b'\n__PAYLOAD__\n',1)
        assert b'\npivas repair-dns\n' in header
        result=subprocess.run(['sh','-n'],input=header,capture_output=True)
        assert result.returncode == 0,result.stderr
        with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
            names=[]
            for m in archive:
                if not m.isfile():continue
                name=Path(m.name).name;names.append(name)
                candidates=[directory/name,ROOT/'dist/common'/name]
                actual=next(p for p in candidates if p.exists())
                assert archive.extractfile(m).read() == actual.read_bytes()
            assert len(names) == 1
print('Verified: 13 build artifacts; source/package parity; both full bundles; two full installer payloads; Xray 26.3.27 including compatible MIPS build; no runtime secrets/cache/logs.')
