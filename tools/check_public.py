#!/usr/bin/env python3
"""Check a curated source tree; report locations, never matched secret values."""
import ast
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
SKIP = {'.git', '.venv', 'node_modules', '__pycache__', 'dist', 'release-assets', 'publication', 'test-results', 'playwright-report'}
FORBIDDEN = {'telegram_bot_config.py', 'auth.json', 'sessions.json', 'pivas.json', 'pivas-groups.json', 'pivas-devices.json', '.env', '.DS_Store'}
PATTERNS = {
    'personal home path': re.compile(r'/(?:Users|home)/[^/\s]+/'),
    'Telegram token': re.compile(r'\b\d{8,12}:[A-Za-z0-9_-]{30,}\b'),
    'GitHub token': re.compile(r'\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{50,}\b'),
    'private key': re.compile(r'-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----'),
}

def inspect(root):
    failures=[];count=0
    for p in sorted(root.rglob('*')):
        rel=p.relative_to(root)
        if any(part in SKIP for part in rel.parts):continue
        if p.is_symlink():
            failures.append(str(rel)+': symlink is not allowed in public source');continue
        if not p.is_file():continue
        if p.name in FORBIDDEN or p.suffix in ('.log','.pem','.key','.ipk','.pyc') or 'backups' in rel.parts:
            failures.append(str(rel)+': private/generated file');continue
        if rel.parts[0]=='vendor' and p.name in ('aarch64-3.10','mipsel-3.4'):continue
        raw=p.read_bytes()
        if len(raw)>10*1024*1024:failures.append(str(rel)+': large file should be a release asset')
        try:text=raw.decode('utf-8')
        except UnicodeDecodeError:
            if p.suffix not in ('.png','.jpg','.webp','.ico'):failures.append(str(rel)+': unexpected binary')
            continue
        count+=1
        for label,pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if (label == 'Telegram token' and rel.as_posix() in ('tests/test_web_api.py', 'tests/web_e2e.cjs')
                        and match.group() == '123456789:' + 'AAFAKE_SECRET_12345678901234567890'):
                    continue
                failures.append('%s:%d: %s' % (rel,text[:match.start()].count('\n')+1,label))
        if p.suffix=='.py':
            try:ast.parse(text,filename=str(rel))
            except SyntaxError as exc:failures.append(str(rel)+': '+str(exc))
        if p.suffix=='.md':
            for match in re.finditer(r'!?\[[^\]]*\]\(([^)]+)\)',text):
                target=unquote(match.group(1).split('#',1)[0])
                if not target or '://' in target or target.startswith('mailto:'):continue
                resolved=(p.parent/target).resolve()
                if not resolved.is_relative_to(root.resolve()) or not resolved.exists():
                    failures.append(str(rel)+': missing/escaping Markdown link '+target)
    return count,failures

def main():
    root=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else ROOT
    count,failures=inspect(root)
    if failures:
        print('\n'.join(failures));return 1
    print('Public source checks: %d text files; no detected private files/tokens, broken local Markdown links or Python syntax errors.' % count)
    print('Pattern checks do not guarantee absence of all secrets; review staged content before publication.')
    return 0

if __name__=='__main__':raise SystemExit(main())
