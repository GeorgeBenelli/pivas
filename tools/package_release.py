#!/usr/bin/env python3
"""Prepare architecture archives and Telegram IPKs; never uploads or publishes."""
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[1]

def write_tar(target,files):
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode='w',format=tarfile.USTAR_FORMAT) as tar:
        for name,p in sorted(files.items()):
            data=p.read_bytes();entry=tarfile.TarInfo(name)
            entry.size=len(data);entry.mode=0o755 if name.endswith('.sh') else 0o644
            entry.mtime=1791504000;entry.uid=entry.gid=0
            tar.addfile(entry,io.BytesIO(data))
    target.write_bytes(gzip.compress(raw.getvalue(),mtime=1791504000))

def main():
    manifest=json.loads((ROOT/'dist/manifest.json').read_text())
    for name,expected in manifest['sha256'].items():
        path=(ROOT/'dist'/name).resolve()
        if not path.is_relative_to((ROOT/'dist').resolve()):raise ValueError('Unsafe manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=expected:raise ValueError('Digest mismatch: '+name)
    out=ROOT/'release-assets';out.mkdir(exist_ok=True)
    label='custom'+manifest['versions']['pivas-full'].rsplit('custom',1)[1]
    common={name:ROOT/name for name in ('README.md','README.en.md','ABOUT.md','RELEASE_NOTES.en.md','INSTALL.md','SETUP.md','LICENSE','THIRD_PARTY_NOTICES.md','CHANGELOG.md','RELEASE_NOTES.md','VALIDATION.md')}
    for directory in ('licenses','docs'):
        for p in (ROOT/directory).rglob('*'):
            if p.is_file() and p.suffix in ('.md','.txt','.json','.png') and not p.name.startswith('ui-'):common[p.relative_to(ROOT).as_posix()]=p
    for directory in ('vendor/xray','vendor/quic-probe'):
        for p in (ROOT/directory).iterdir():
            if p.is_file() and p.name not in ('aarch64-3.10','mipsel-3.4'):common[p.relative_to(ROOT).as_posix()]=p
    for name in ('go.mod','go.sum','main.go','main_test.go'):
        common['tools/quic-probe/'+name]=ROOT/'tools/quic-probe'/name
    common.update({name:ROOT/name for name in ('SECURITY.md','CONTRIBUTING.md','repository.json')})
    generated=[]
    for arch in ('mipsel','aarch64','common'):
        files=dict(common)
        files.update({Path(name).name:ROOT/'dist'/name for name in manifest['sha256'] if Path(name).parts[0]==arch})
        files['manifest.json']=ROOT/'dist/manifest.json'
        name='pivas-'+label+'-'+arch+'.tar.gz';write_tar(out/name,files);generated.append(name)
    # The Telegram bot accepts IPK documents, not the architecture tarballs.
    for arch in ('mipsel','aarch64'):
        package=next((ROOT/'dist'/arch).glob('pivas-full_*.ipk'))
        shutil.copyfile(package,out/package.name);generated.append(package.name)
    for name in ('RELEASE_NOTES.md','RELEASE_NOTES.en.md','THIRD_PARTY_NOTICES.md','LICENSE'):
        shutil.copyfile(ROOT/name,out/name);generated.append(name)
    shutil.copyfile(ROOT/'dist/manifest.json',out/'manifest.json');generated.append('manifest.json')
    sums=''.join(hashlib.sha256((out/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in sorted(generated))
    (out/'SHA256SUMS').write_text(sums)
    print('Prepared',len(generated)+1,'files in',out)
    print('Not published. Check THIRD_PARTY_NOTICES.md before public distribution.')

if __name__=='__main__':main()
