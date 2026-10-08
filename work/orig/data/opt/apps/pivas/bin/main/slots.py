"""Names belong to numbered routes, independently of their VLESS connections."""
import hashlib
import json
import re

CONFIG = '/opt/etc/pivas-slots.json'
DEFAULTS = {'1': 'Слот 1', '2': 'Слот 2'}


def name(value):
    if not isinstance(value, str):
        raise ValueError('Название слота должно быть текстом')
    if re.search(r'[\x00-\x1f\x7f-\x9f\u00ad\u061c\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]', value):
        raise ValueError('Название слота: от 1 до 32 символов без переносов строк')
    value = value.strip()
    if not 1 <= len(value) <= 32:
        raise ValueError('Название слота: от 1 до 32 символов без переносов строк')
    return value


def validate(names):
    if not isinstance(names, dict) or set(names) != {'1', '2'}:
        raise ValueError('Нужны названия двух слотов')
    return {k: name(v) for k, v in names.items()}


def load(rt):
    p = rt.path(CONFIG)
    if not p.exists():
        return dict(DEFAULTS)
    if p.stat().st_size > 2048:
        raise ValueError('Файл названий слотов повреждён')
    value = json.loads(p.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or value.get('version') != 1 or set(value) != {'version', 'names'}:
        raise ValueError('Неизвестный формат названий слотов')
    return validate(value['names'])


def encode(names):
    return (json.dumps({'version': 1, 'names': validate(names)}, ensure_ascii=False) + '\n').encode()


def view(rt):
    names = load(rt)
    return {'names': names, 'revision': hashlib.sha256(encode(names)).hexdigest()}


def handle(rt, request=None):
    state = view(rt)
    if request is None:
        return state
    if request.get('revision') != state['revision']:
        raise ValueError('Названия слотов изменились. Обновите список и повторите')
    slot = request.get('slot')
    if type(slot) is not int or slot not in (1, 2):
        raise ValueError('Укажите слот 1 или 2')
    action = request.get('action')
    if action not in ('rename', 'reset'):
        raise ValueError('Неизвестное действие с названием слота')
    names = state['names']
    names[str(slot)] = DEFAULTS[str(slot)] if action == 'reset' else name(request.get('name'))
    if names != load(rt):
        rt.atomic(rt.path(CONFIG), encode(names))
    return view(rt)


def cli(rt, args):
    if args in ([], ['list']):
        return view(rt)
    if len(args) >= 2 and args[0] in ('name', 'reset'):
        if args[1] not in ('1', '2') or len(args) != (3 if args[0] == 'name' else 2):
            raise ValueError('pivas slots name <1|2> <название> | reset <1|2>')
        request = {'revision': view(rt)['revision'], 'slot': int(args[1]),
                   'action': 'rename' if args[0] == 'name' else 'reset'}
        if args[0] == 'name': request['name'] = args[2]
        return handle(rt, request)
    raise ValueError('pivas slots list | name <1|2> <название> | reset <1|2>')
