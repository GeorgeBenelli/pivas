#!/usr/bin/env python3
"""Vendor pinned Xray; rebuild MIPS with a runtime compatible with Keenetic."""
import argparse
import hashlib
import json
import tempfile
from pathlib import Path
import struct
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '26.3.27'
ASSETS = {
    'mipsel-3.4': ('mips32le', 'xray_softfloat', 'fe1ded07a64fe0a406c6c1089f09b6c2999fc2309509ca4c98d93469c0cbf9df', 1, 8),
    'aarch64-3.10': ('arm64-v8a', 'xray', '4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c', 2, 183),
}

def fetch(cache):
    dest = ROOT / 'vendor/xray'
    dest.mkdir(parents=True, exist_ok=True)
    records = {}
    for arch, (asset, member, expected, elf_class, machine) in ASSETS.items():
        name = 'Xray-linux-' + asset + '.zip'
        url = 'https://github.com/XTLS/Xray-core/releases/download/v' + VERSION + '/' + name
        # Both the official asset name and compatibility-check cache are accepted.
        candidates = [cache / name, cache / ('v' + VERSION + '-' + asset + '.zip')]
        cached = next((p for p in candidates if p.is_file()), None)
        if cached is None:
            cache.mkdir(parents=True, exist_ok=True)
            cached = candidates[0]
            with urlopen(url, timeout=120) as response:
                raw = response.read()
            cached.write_bytes(raw)
        raw = cached.read_bytes()
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Archive digest mismatch: ' + name)
        with zipfile.ZipFile(cached) as archive:
            binary = archive.read(member)
            if binary[:4] != b'\x7fELF' or binary[4] != elf_class or binary[5] != 1 or struct.unpack_from('<H', binary, 18)[0] != machine:
                raise ValueError('Wrong ELF architecture: ' + arch)
            target = dest / arch
            target.write_bytes(binary); target.chmod(0o755)
            for notice in ('LICENSE', 'README.md'):
                content = archive.read(notice)
                output = dest / notice
                if output.exists() and output.read_bytes() != content:
                    raise ValueError('Inconsistent upstream notice: ' + notice)
                output.write_bytes(content)
        records[arch] = {'url': url, 'archive_sha256': expected, 'member': member,
                         'binary_sha256': hashlib.sha256(binary).hexdigest(), 'size': len(binary),
                         'elf_class': elf_class, 'elf_machine': machine,
                         'go': '1.26.1', 'float': 'softfloat' if elf_class == 1 else None}
    notices = {n: hashlib.sha256((dest/n).read_bytes()).hexdigest() for n in ('LICENSE', 'README.md')}
    (dest/'manifest.json').write_text(json.dumps({'version': VERSION, 'assets': records, 'notices': notices}, indent=2)+'\n')
    print('Verified official Xray', VERSION, 'for mipsel softfloat and aarch64')
    from build_xray_mips import build
    build(cache)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--cache', type=Path, default=Path(tempfile.gettempdir()) / 'pivas-xray-downloads')
    fetch(parser.parse_args().cache)
