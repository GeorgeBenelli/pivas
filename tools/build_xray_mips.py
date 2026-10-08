#!/usr/bin/env python3
"""Build pinned upstream Xray with the Go runtime compatible with Keenetic MIPS."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
VERSION = '26.3.27'
GO_VERSION = 'go1.26.6'
SOURCE_URL = 'https://codeload.github.com/XTLS/Xray-core/tar.gz/refs/tags/v' + VERSION
SOURCE_SHA256 = '992a4997e6bb846d11469435d687f99ef812fcde1e0a009bb8e95189ea20331d'
FLAGS = ['-trimpath', '-buildvcs=false', '-gcflags=-l=4',
         '-ldflags=-X github.com/xtls/xray-core/core.build=pivas-26.3.27-go1.26.6 -s -w -buildid=']


def build(cache, source_archive=None):
    go = os.environ.get('GO_BINARY') or shutil.which('go') or str(Path.home() / '.local/go/bin/go')
    compiler = subprocess.check_output([go, 'version'], text=True).strip()
    if compiler.split()[2] != GO_VERSION:
        raise ValueError('Use ' + GO_VERSION + ' via GO_BINARY; got ' + compiler)
    cache.mkdir(parents=True, exist_ok=True)
    archive = source_archive or cache / ('Xray-core-' + VERSION + '.tar.gz')
    if not archive.is_file():
        with urlopen(SOURCE_URL, timeout=120) as response:
            archive.write_bytes(response.read())
    if hashlib.sha256(archive.read_bytes()).hexdigest() != SOURCE_SHA256:
        raise ValueError('Upstream Xray source archive digest mismatch')
    env = dict(os.environ, GOOS='linux', GOARCH='mipsle', GOMIPS='softfloat',
               CGO_ENABLED='0', GOTOOLCHAIN='local', GOCACHE=os.environ.get('GOCACHE', str(Path(tempfile.gettempdir()) / 'pivas-go-cache')),
               GOMODCACHE=os.environ.get('GOMODCACHE', str(Path(tempfile.gettempdir()) / 'pivas-go-mod')),
               GOPATH=os.environ.get('GOPATH', str(Path(tempfile.gettempdir()) / 'pivas-go')))
    dest = ROOT / 'vendor/xray'
    manifest = json.loads((dest / 'manifest.json').read_text())
    if manifest['version'] != VERSION:
        raise ValueError('Fetch pinned official Xray assets first')
    with tempfile.TemporaryDirectory(prefix='pivas-xray-source-', dir=cache) as temporary:
        extracted = Path(temporary)
        with tarfile.open(archive) as source:
            for member in source:
                target = (extracted / member.name).resolve()
                if not target.is_relative_to(extracted.resolve()) or member.issym() or member.islnk():
                    raise ValueError('Unsafe upstream archive member: ' + member.name)
                source.extract(member, extracted)
        subprocess.run([go, 'build', *FLAGS, '-o', str(extracted / 'xray'), './main'],
                       cwd=extracted / ('Xray-core-' + VERSION), env=env, check=True)
        binary = (extracted / 'xray').read_bytes()
    if binary[:6] != b'\x7fELF\x01\x01':
        raise ValueError('Expected little endian 32-bit ELF')
    record = manifest['assets']['mipsel-3.4']
    record['upstream_binary_sha256'] = record.get('upstream_binary_sha256', record['binary_sha256'])
    record.update(origin='source-build', go='1.26.6', compiler=compiler,
                  source_url=SOURCE_URL, source_sha256=SOURCE_SHA256, build_flags=FLAGS,
                  binary_sha256=hashlib.sha256(binary).hexdigest(), size=len(binary))
    target = dest / 'mipsel-3.4'
    target.write_bytes(binary)
    target.chmod(0o755)
    go_root = Path(subprocess.check_output([go, 'env', 'GOROOT'], text=True).strip())
    notice = (go_root / 'LICENSE').read_bytes()
    (dest / 'GO-LICENSE').write_bytes(notice)
    manifest['notices']['GO-LICENSE'] = hashlib.sha256(notice).hexdigest()
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('Built upstream Xray', VERSION, 'with', GO_VERSION, 'for Keenetic MIPS softfloat')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=Path(tempfile.gettempdir()) / 'pivas-xray-downloads')
    parser.add_argument('--source-archive', type=Path)
    args = parser.parse_args()
    build(args.cache, args.source_archive)
