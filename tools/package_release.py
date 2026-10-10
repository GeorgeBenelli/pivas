#!/usr/bin/env python3
"""Prepare only full installers, full update packages and their checksums."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    dist = (ROOT / 'dist').resolve()
    manifest = json.loads((dist / 'manifest.json').read_text())
    for name, expected in manifest['sha256'].items():
        path = (dist / name).resolve()
        if not path.is_relative_to(dist):
            raise ValueError('Unsafe manifest path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Digest mismatch: ' + name)
    selected = {}
    for arch in ('mipsel', 'aarch64'):
        package = next((dist / arch).glob('pivas-full_*.ipk'))
        selected[package.name] = package
        selected['install-pivas-full-' + arch + '.sh'] = dist / arch / 'install-pivas-full.sh'
    out = ROOT / 'release-assets'
    if out.is_symlink():
        raise ValueError('Release output must not be a symlink')
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    digests = {}
    for name, source in sorted(selected.items()):
        shutil.copyfile(source, out / name)
        digests[name] = hashlib.sha256(source.read_bytes()).hexdigest()
    public_manifest = {'versions': manifest['versions'], 'sha256': digests}
    (out / 'manifest.json').write_text(json.dumps(public_manifest, indent=2) + '\n')
    digests['manifest.json'] = hashlib.sha256((out / 'manifest.json').read_bytes()).hexdigest()
    (out / 'SHA256SUMS').write_text(''.join(digest + '  ' + name + '\n' for name, digest in sorted(digests.items())))
    print('Prepared 6 release files:', out)
    print('Two full installers, two full update IPKs, manifest.json and SHA256SUMS. Not published.')


if __name__ == '__main__':
    main()
