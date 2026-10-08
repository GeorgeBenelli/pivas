"""Recursively inspect sources and nested packages for names supplied as arguments.

ELF binaries are third-party payloads, verified unchanged by verify_dist.py.
Compressed archive bytes and rendered images are not treated as plain text.
"""
from pathlib import Path
import io
import re
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
terms = sys.argv[1:]
if not terms:
    raise SystemExit('Pass the historical names to reject as command-line arguments')
pattern = re.compile('|'.join(re.escape(t) for t in terms), re.I)
violations = []
checked = 0
elf = 0


def text_check(label, data):
    global checked, elf
    if data.startswith(b'\x7fELF'):
        elf += 1
        return
    if data.startswith(b'\x89PNG') or label.endswith('.pyc'):
        return
    try:
        content = data.decode('utf-8')
    except UnicodeDecodeError:
        return
    checked += 1
    if pattern.search(content):
        violations.append(label)


def inspect(label, data):
    if pattern.search(label):
        violations.append(label + ' [filename]')
    marker = b'\n__PAYLOAD__\n'
    if label.endswith('.sh') and marker in data:
        header, payload = data.split(marker, 1)
        text_check(label + ':header', header)
        inspect(label + ':payload.tar.gz', payload)
    elif data.startswith(b'\x1f\x8b') and label.endswith(('.ipk', '.tar.gz')):
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            for member in archive:
                nested = label + ':' + member.name
                if member.isfile():
                    inspect(nested, archive.extractfile(member).read())
                else:
                    if pattern.search(member.name + ' ' + member.linkname):
                        violations.append(nested + ' [entry]')
    else:
        text_check(label, data)


for path in sorted(ROOT.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts:
        continue
    inspect(path.relative_to(ROOT).as_posix(), path.read_bytes())
if violations:
    print('\n'.join(violations))
    raise SystemExit('Historical names remain in %d entries' % len(violations))
print('Verified: %d text entries, including nested IPK/installers; no rejected names. %d third-party ELF occurrences left intact.' % (checked, elf))
