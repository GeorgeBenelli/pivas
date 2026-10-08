#!/usr/bin/env python3
"""Build the one-shot QUIC probe with pinned dependencies; no router service."""
import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
GO = os.environ.get('GO_BINARY') or shutil.which('go') or str(Path.home() / '.local/go/bin/go')
ENV = dict(os.environ, GOCACHE=os.environ.get('GOCACHE', str(Path(tempfile.gettempdir()) / 'pivas-go-cache')),
           GOMODCACHE=os.environ.get('GOMODCACHE', str(Path(tempfile.gettempdir()) / 'pivas-go-mod')),
           GOPATH=os.environ.get('GOPATH', str(Path(tempfile.gettempdir()) / 'pivas-go')))
SOURCE = ROOT / 'tools/quic-probe'

def build():
    subprocess.run([GO, 'test', './...'], cwd=SOURCE, env=ENV, check=True)
    out = ROOT / 'vendor/quic-probe'
    out.mkdir(parents=True, exist_ok=True)
    # The distribution carries the notices required by the static dependencies.
    notices = []
    for name, version in [('github.com/apernet/quic-go','v0.57.2-0.20260111184307-eec823306178'),
                          ('golang.org/x/crypto','v0.47.0'), ('golang.org/x/net','v0.48.0'),
                          ('golang.org/x/sys','v0.40.0')]:
        license_file = Path(ENV['GOMODCACHE']) / (name + '@' + version) / 'LICENSE'
        notices.append(name + ' ' + version + '\n' + license_file.read_text())
    go_root = subprocess.check_output([GO, 'env', 'GOROOT'], env=ENV, text=True).strip()
    notices.append('Go standard library\n' + (Path(go_root) / 'LICENSE').read_text())
    (out / 'LICENSES.txt').write_text('\n\n'.join(notices))
    for label, arch in [('mipsel-3.4', 'mipsle'), ('aarch64-3.10', 'arm64')]:
        env = dict(ENV, GOOS='linux', GOARCH=arch, CGO_ENABLED='0', GOMIPS='softfloat')
        subprocess.run([GO, 'build', '-trimpath', '-ldflags=-s -w -buildid=',
                        '-o', str(out / label), '.'], cwd=SOURCE, env=env, check=True)
    subprocess.run([GO, 'build', '-trimpath', '-o', str(Path(tempfile.gettempdir()) / 'pivas-quic-probe'), '.'],
                   cwd=SOURCE, env=ENV, check=True)
    inputs = [p for p in SOURCE.iterdir() if p.suffix == '.go' or p.name in ('go.mod','go.sum')]
    (out / 'manifest.json').write_text(json.dumps({
        'compiler': subprocess.check_output([GO, 'version'], text=True).strip(),
        'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(inputs)},
        'sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.name != 'manifest.json'},
    }, indent=2) + '\n')

if __name__ == '__main__':
    build()
