import configparser
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import threading
import sys
from contextlib import suppress
from logging.handlers import RotatingFileHandler

import requests
import telebot
from telebot import apihelper, types
from telebot.formatting import mbold, mcode, mlink
from telebot.handler_backends import BaseMiddleware, CancelUpdate
from telebot.types import InputFile

import telegram_bot_config

BOT_VERSION = "1.2-custom14"

# Весь трафик telebot к api.telegram.org пускаем через HTTP-inbound xray
# (slot-2 — тот, через который гарантированно виден Telegram). Порт и
# адрес задаются в конфиге бота, если не указаны — используется локальный
# xray по умолчанию.
_proxy_url = getattr(telegram_bot_config, "proxy_url", "http://127.0.0.1:1098")
if _proxy_url:
    apihelper.proxy = {"http": _proxy_url, "https": _proxy_url}

config = configparser.ConfigParser()
CONFIG_PATH = "/opt/etc/telegram4pivas/config.ini"
FLAVOR_FILE = "/opt/etc/telegram4pivas/.flavor"  # 'web' | 'noweb' (создаётся .ipk postinst'ом)


def _detect_web_flavor():
    """
    Включена ли Web-фича (кнопка «Веб», web_toggle, setpass).
    Наличие pivas-web на роутере важнее старого .flavor: на обновлениях
    с noweb-сборки мог остаться /opt/etc/telegram4pivas/.flavor=noweb, и он
    скрывал кнопку «Веб» даже после install-pivas-web.sh.
    """
    if os.path.exists("/opt/etc/pivas-web/server.py"):
        return True
    try:
        with open(FLAVOR_FILE) as f:
            v = f.read().strip().lower()
            if v == "web":
                return True
    except Exception:
        pass
    return False


WEB_FEATURE_ENABLED = _detect_web_flavor()

logger = logging.getLogger(name="telegram4pivas")
logger.setLevel(logging.DEBUG)

handler = RotatingFileHandler(
    filename="/opt/etc/telegram4pivas/telegram4pivas_log.txt",
    maxBytes=1 * 1024 * 1024,
    backupCount=3,
    encoding="UTF-8",
)
formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

user_states = {}
handler_called = False
_vless_swap_lock = threading.Lock()
_group_drafts = {}
_GROUP_PAGE_SIZE = 8
_GROUP_DOMAIN_PAGE_SIZE = 10
_GROUP_RUNTIME = "/opt/apps/pivas/bin/main/runtime.py"

bot = telebot.TeleBot(
    telegram_bot_config.token,
    use_class_middlewares=True,
)


class Middleware(BaseMiddleware):
    def __init__(self):
        self.update_types = ["message"]

    def _get_admins(self):
        admins = []
        with suppress(Exception):
            config.read(CONFIG_PATH, encoding="UTF-8")
            admins.extend([int(a) for a in config.get("ADMINS", "users_ids").split(",")])
        admins.extend(telegram_bot_config.userid)
        return admins

    def pre_process(self, message: types.Message, data: dict):
        logger.debug("Processing message from %s", message.from_user.username)
        admins = self._get_admins()
        if not admins:
            # CVE-worthy: раньше первый написавший боту автоматически
            # становился админом. Если злоумышленник знал токен (утечка
            # git/бэкапа) — он получал root-терминал на роутере.
            # Теперь: пустой whitelist = бот никого не пускает.
            logger.error(
                "Список админов пуст! Укажи userid=[<твой_id>] в "
                "/opt/etc/telegram4pivas/telegram_bot_config.py и "
                "перезапусти бот: /opt/etc/init.d/S98telegram4pivas restart"
            )
            try:
                bot.send_message(
                    message.chat.id,
                    "⚠️ Бот не настроен: нет списка админов. Обратитесь к владельцу роутера.",
                )
            except Exception:
                pass
            return CancelUpdate()

        if message.from_user.id not in admins:
            logger.warning(
                "Unauthorized access attempt by %s",
                message.from_user.username,
            )
            bot.send_message(message.chat.id, "Вы не авторизованы")

            username = "Неизвестно"
            if message.from_user.username is not None:
                username = f"@{message.from_user.username}"

            user_link = f"[{message.from_user.full_name}](tg://user?id={message.from_user.id})"

            for id in admins:
                bot.send_message(
                    id,
                    f"Попытка неавторизованного доступа:\n{user_link}\\({username}\\), UserID: {message.from_user.id}",
                    parse_mode="MarkdownV2",
                )
            return CancelUpdate()

    def post_process(self, message, data, exception):
        if exception:
            logger.error("Error in processing message: %s", str(exception))


def send_startup_message():
    admins = Middleware._get_admins('')
    startMenu = types.ReplyKeyboardMarkup(resize_keyboard=True)
    item1 = types.KeyboardButton("Управление хостами")
    item2 = types.KeyboardButton("Управление подключениями")
    item3 = types.KeyboardButton("Сервис")
    startMenu.add(item1, item2, item3)

    for id in admins:
        bot.send_message(id,
                         f"Бот запущен, версия: {BOT_VERSION}",
                         reply_markup=startMenu,
                         )


@bot.message_handler(commands=["start"], chat_types=["private"])
def handle_start(message: types.Message):
    try:
        logger.info("User %s started the bot", message.from_user.username)
        startMenu = types.ReplyKeyboardMarkup(resize_keyboard=True)
        item1 = types.KeyboardButton("Управление хостами")
        item2 = types.KeyboardButton("Управление подключениями")
        item3 = types.KeyboardButton("Сервис")
        startMenu.add(item1, item2, item3)
        bot.send_message(
            message.chat.id,
            "Панель управления ПИВАС",
            reply_markup=startMenu,
        )
    except Exception as e:
        logger.exception("Error in handle_start: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def _hosts_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=3)
    kb.add(
        types.KeyboardButton("Slot-1: +домен"),
        types.KeyboardButton("Slot-1: -домен"),
        types.KeyboardButton("Slot-1: список"),
    )
    kb.add(
        types.KeyboardButton("Slot-2: +домен"),
        types.KeyboardButton("Slot-2: -домен"),
        types.KeyboardButton("Slot-2: список"),
    )
    kb.add(
        types.KeyboardButton("Все домены"),
        types.KeyboardButton("Очистить список"),
    )
    kb.add(types.KeyboardButton("Группы доменов"))
    kb.add(
        types.KeyboardButton("Импорт"),
        types.KeyboardButton("Экспорт"),
    )
    kb.add(types.KeyboardButton("Назад"))
    return kb


@bot.message_handler(regexp="Управление хостами", chat_types=["private"])
def hosts_message(message: types.Message):
    try:
        logger.info("User %s requested host management", message.from_user.username)
        bot.send_message(
            message.chat.id,
            "Управление хостами.\n"
            "Домен может быть в одном из трёх состояний: через slot-1, через slot-2, или вне VPN.\n"
            "«+домен» добавляет (или переносит между слотами). «-домен» полностью убирает из VPN.\n"
            "«Группы доменов» управляют несколькими доменами как одним набором.",
            reply_markup=_hosts_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in hosts_message: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


class GroupMenuError(Exception):
    pass


def _slot_state(request=None):
    args = ['/opt/bin/python3', _GROUP_RUNTIME, 'slots']
    if request is not None: args.append('write')
    try:
        result = subprocess.run(args, input=json.dumps(request, ensure_ascii=False) if request is not None else None,
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            detail = (result.stderr or '').strip().splitlines()[-1:] or ['']
            if any(word in detail[0] for word in ('Название', 'Названия', 'слота')):
                raise GroupMenuError(detail[0].removeprefix('Pivas: ')[:200])
            raise GroupMenuError('Не удалось получить названия слотов. Проверьте версию ядра Pivas.')
        state = json.loads(result.stdout)
        if set(state['names']) != {'1', '2'} or not isinstance(state['revision'], str): raise ValueError()
        if any(not isinstance(n, str) or not 1 <= len(n) <= 32 for n in state['names'].values()): raise ValueError()
        return state
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        raise GroupMenuError('Не удалось получить названия слотов. Попробуйте ещё раз.') from None


def _slot_labels():
    try: return _slot_state()['names']
    except GroupMenuError: return {'1': 'Слот 1', '2': 'Слот 2'}


def _slot_label(slot, names=None):
    value = (names if names is not None else _slot_labels())[str(slot)]
    return value if value == f'Слот {slot}' else f'{slot} · {value}'


def _slot_show(chat_id, state=None):
    state = state or _slot_state()
    kb = types.InlineKeyboardMarkup(row_width=1)
    for number in (1, 2):
        kb.add(_group_button('✎ ' + _slot_label(number, state['names']),
                             f"sname:rename:{number}:{state['revision'][:10]}"))
    bot.send_message(chat_id, 'Названия слотов\nВыберите слот для переименования.\n'
                     'При обмене ссылками названия остаются у своих слотов.', reply_markup=kb)


@bot.message_handler(regexp='^Названия слотов$', chat_types=['private'])
def slot_names_menu(message):
    if not _group_authorized(message.from_user, message.chat): return
    try: _slot_show(message.chat.id)
    except GroupMenuError as exc: bot.send_message(message.chat.id, str(exc))


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith('sname:'))
def slot_name_callback(call):
    if not _group_authorized(call.from_user, call.message.chat):
        bot.answer_callback_query(call.id, 'Доступ запрещён', show_alert=True)
        return
    try:
        parts = call.data.split(':')
        if len(parts) != 4 or parts[1] != 'rename' or parts[2] not in ('1', '2'): raise GroupMenuError('Откройте названия слотов заново')
        state = _slot_state()
        if parts[3] != state['revision'][:10]: raise GroupMenuError('Названия слотов изменились. Откройте меню заново')
        slot = int(parts[2])
        bot.answer_callback_query(call.id)
        answer = bot.send_message(call.message.chat.id, f'Новое название для {_slot_label(slot, state["names"])}?\n'
                                  'От 1 до 32 символов. /default вернёт стандартное название.', reply_markup=_cancel_kb())
        bot.register_next_step_handler(answer, _slot_name_text, slot, state['revision'], call.from_user.id)
    except GroupMenuError as exc:
        bot.answer_callback_query(call.id, str(exc), show_alert=True)


def _slot_name_text(message, slot, revision, owner):
    if not _group_authorized(message.from_user, message.chat) or message.from_user.id != owner: return
    if _is_cancel(message.text or ''):
        bot.send_message(message.chat.id, 'Переименование отменено.', reply_markup=_connections_keyboard())
        return
    try:
        value = message.text or ''
        state = _slot_state({'action': 'reset' if value.strip() == '/default' else 'rename',
                             'slot': slot, 'name': value, 'revision': revision})
        bot.send_message(message.chat.id, 'Название сохранено: ' + _slot_label(slot, state['names']),
                         reply_markup=_connections_keyboard())
        _slot_show(message.chat.id, state)
    except GroupMenuError as exc:
        if str(exc).startswith(('Pivas: Название слота', 'Название слота')):
            answer = bot.send_message(message.chat.id, str(exc) + '. Пришлите другое название или нажмите «Отмена».', reply_markup=_cancel_kb())
            bot.register_next_step_handler(answer, _slot_name_text, slot, revision, owner)
        else:
            bot.send_message(message.chat.id, str(exc), reply_markup=_connections_keyboard())


def _group_catalog(request=None):
    argv = ["/opt/bin/python3", _GROUP_RUNTIME, "catalog"]
    if request is not None:
        argv.append("write")
    try:
        result = subprocess.run(argv, input=json.dumps(request, ensure_ascii=False) if request is not None else None,
                                capture_output=True, text=True, timeout=50)
    except (OSError, subprocess.TimeoutExpired):
        raise GroupMenuError("Не удалось связаться с ядром Pivas. Попробуйте ещё раз.")
    if result.returncode:
        # Errors from the catalogue are expected and safe to show; never relay
        # arbitrary command output, which might contain router credentials.
        line = (result.stderr or "").strip().splitlines()[-1:] or [""]
        detail = line[0].removeprefix("Pivas: ")
        if any(word in detail for word in ("Группа", "групп", "Домен", "домен", "Список изменился", "слот", "записей", "подсет", "IPv6")):
            raise GroupMenuError(detail[:240])
        raise GroupMenuError("Не удалось изменить группы. Проверьте диагностический отчёт.")
    try:
        state = json.loads(result.stdout)
        if not isinstance(state, dict) or not isinstance(state.get("groups"), list) or not isinstance(state.get("revision"), str):
            raise ValueError("invalid catalogue result")
        return state
    except (ValueError, TypeError):
        raise GroupMenuError("Ядро Pivas вернуло неожиданный ответ.")


def _group_authorized(user, chat):
    return chat.type == "private" and user.id in Middleware()._get_admins()


def _group_button(text, data):
    return types.InlineKeyboardButton(text, callback_data=data)


def _group_find(state, group_id):
    return next((g for g in state["groups"] if g["id"] == group_id), None)


def _group_index(state, page=0):
    names = _slot_labels()
    groups = sorted(state["groups"], key=lambda g: g["name"].casefold())
    pages = max(1, (len(groups) + _GROUP_PAGE_SIZE - 1) // _GROUP_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(_group_button("＋ Создать группу", "grp:new"))
    for g in groups[page * _GROUP_PAGE_SIZE:(page + 1) * _GROUP_PAGE_SIZE]:
        status = "Вкл" if g["enabled"] else "Пауза"
        label = g['name'] if len(g['name']) <= 40 else g['name'][:39] + '…'
        kb.add(_group_button(f"{status} · {label} · {len(g['domains'])} · {_slot_label(g['slot'], names)}", f"grp:view:{g['id']}:0"))
    if pages > 1:
        row = []
        if page: row.append(_group_button("←", f"grp:list:{page - 1}"))
        row.append(_group_button(f"{page + 1}/{pages}", "grp:noop"))
        if page + 1 < pages: row.append(_group_button("→", f"grp:list:{page + 1}"))
        kb.row(*row)
    text = (f"Группы доменов: {len(groups)}\n"
            "Откройте группу, чтобы управлять её доменами, слотом и состоянием.\n"
            "«Вкл» — группа работает · «Пауза» — временно выключена")
    if state.get("drift"):
        text += "\n⚠ Списки изменены вне групп; изменения заблокированы до исправления."
    return text, kb


def _group_detail(state, group, page=0):
    names = _slot_labels()
    domains = group["domains"]
    pages = max(1, (len(domains) + _GROUP_DOMAIN_PAGE_SIZE - 1) // _GROUP_DOMAIN_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    listed = domains[page * _GROUP_DOMAIN_PAGE_SIZE:(page + 1) * _GROUP_DOMAIN_PAGE_SIZE]
    text = (f"Группа: {group['name']}\n{_slot_label(group['slot'], names)} · "
            f"{'работает' if group['enabled'] else 'приостановлена'} · {len(domains)} записей\n\n"
            + "\n".join(listed) + f"\n\nЗаписи: страница {page + 1}/{pages}")
    gid = group["id"]
    rev = state["revision"][:10]
    kb = types.InlineKeyboardMarkup(row_width=2)
    if pages > 1:
        row = []
        if page: row.append(_group_button("← Домены", f"grp:view:{gid}:{page - 1}"))
        if page + 1 < pages: row.append(_group_button("Домены →", f"grp:view:{gid}:{page + 1}"))
        kb.row(*row)
    kb.row(_group_button("＋ Добавить записи", f"grp:add:{gid}:{rev}"),
           _group_button("− Убрать записи", f"grp:remove:{gid}:{rev}"))
    kb.row(_group_button("⇄ Перенести: " + _slot_label(3 - group['slot'], names), f"grp:move:{gid}:{3 - group['slot']}:{rev}"),
           _group_button("▶ Включить" if not group["enabled"] else "⏸ Приостановить",
                         f"grp:toggle:{gid}:{rev}"))
    kb.row(_group_button("✎ Переименовать", f"grp:rename:{gid}:{rev}"),
           _group_button("🗑 Удалить группу", f"grp:delete:{gid}:{rev}"))
    kb.add(_group_button("← Все группы", "grp:list:0"))
    return text, kb


def _group_show(chat_id, state=None, page=0, group_id=None, call=None, notice=None):
    state = state or _group_catalog()
    group = _group_find(state, group_id) if group_id else None
    text, kb = _group_detail(state, group, page) if group else _group_index(state, page)
    if notice: text = notice + "\n\n" + text
    if call is not None:
        try:
            bot.edit_message_text(text, chat_id, call.message.message_id, reply_markup=kb)
            return
        except Exception:
            pass
    _send_with_retry(chat_id, text, reply_markup=kb)


def _group_mutate(request):
    return _group_catalog(request)


def _group_stale(state, short_revision):
    if not state["revision"].startswith(short_revision):
        raise GroupMenuError("Группы изменились. Откройте свежий список и повторите действие.")


def _group_prompt(chat_id, text, handler, *args):
    answer = _send_with_retry(chat_id, text, reply_markup=_cancel_kb())
    bot.register_next_step_handler(answer, handler, *args)


def _group_cancel_step(message):
    if _is_cancel(message.text) or _looks_like_menu_nav(message.text):
        _group_drafts.pop(message.chat.id, None)
        _send_with_retry(message.chat.id, "Действие с группой отменено.", reply_markup=_hosts_keyboard())
        return True
    return False


def _group_domains_from_message(message):
    text = (message.text or "") + "\n" + (message.caption or "")
    doc = getattr(message, "document", None)
    if doc:
        if doc.file_size and doc.file_size > _DOMAIN_FILE_MAX:
            return [], "Файл слишком большой: максимум 512 КБ."
        name = doc.file_name or ""
        if name and not re.search(r"\.(txt|list|lst|csv|conf)$", name, re.I) and not (doc.mime_type or "").startswith("text/"):
            return [], "Пришлите текстовый файл со списком доменов."
        try:
            data = bot.download_file(bot.get_file(doc.file_id).file_path)
            if len(data) > _DOMAIN_FILE_MAX:
                return [], "Файл слишком большой: максимум 512 КБ."
            text += "\n" + data.decode("utf-8", errors="replace")
        except Exception:
            return [], "Не удалось прочитать файл. Попробуйте ещё раз."
    values = []
    for line in text.splitlines():
        values.extend(v for v in re.split(r"[\s,;]+", line.split("#", 1)[0]) if v)
    if len(values) > 2000:
        return [], "За одну операцию можно обработать не более 2000 доменов."
    return list(dict.fromkeys(values)), None


def _group_domain_key(value):
    from urllib.parse import urlsplit
    value = value.strip().lower()
    if value.startswith(("http://", "https://")):
        value = urlsplit(value).hostname or ""
    if value.startswith("*."):
        value = value[2:]
    return value.rstrip(".").encode("idna").decode("ascii")


@bot.message_handler(regexp=r"^Группы доменов$", chat_types=["private"])
def group_menu(message: types.Message):
    if not _group_authorized(message.from_user, message.chat): return
    try:
        _group_show(message.chat.id)
    except GroupMenuError as exc:
        bot.send_message(message.chat.id, str(exc), reply_markup=_hosts_keyboard())


def _group_create_name(message):
    if not _group_authorized(message.from_user, message.chat) or _group_cancel_step(message): return
    name = (message.text or "").strip()
    if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
        _group_prompt(message.chat.id, "Название: от 1 до 80 символов. Пришлите другое или нажмите «Отмена».", _group_create_name)
        return
    _group_drafts[message.chat.id] = {"owner": message.from_user.id, "name": name}
    kb = types.InlineKeyboardMarkup(row_width=2)
    names = _slot_labels()
    kb.row(_group_button(_slot_label(1, names), "grp:newslot:1"), _group_button(_slot_label(2, names), "grp:newslot:2"))
    bot.send_message(message.chat.id, f"Группа «{name}». В какой слот её добавить?", reply_markup=kb)


def _group_create_domains(message):
    if not _group_authorized(message.from_user, message.chat) or _group_cancel_step(message): return
    draft = _group_drafts.get(message.chat.id)
    if not draft or draft["owner"] != message.from_user.id or "slot" not in draft:
        bot.send_message(message.chat.id, "Создание группы прервано. Откройте меню групп снова.")
        return
    domains, error = _group_domains_from_message(message)
    if error or not domains:
        _group_prompt(message.chat.id, error or "Пришлите хотя бы один домен или .txt-файл.", _group_create_domains)
        return
    try:
        state = _group_catalog()
        result = _group_mutate({"revision": state["revision"], "action": "create", "name": draft["name"],
                                "slot": draft["slot"], "domains": domains})
        group = next(g for g in result["groups"] if g["name"] == draft["name"])
        _group_drafts.pop(message.chat.id, None)
        _group_show(message.chat.id, result, group_id=group["id"], notice="Группа создана.")
    except GroupMenuError as exc:
        _group_drafts.pop(message.chat.id, None)
        bot.send_message(message.chat.id, str(exc), reply_markup=_hosts_keyboard())
    except Exception:
        _group_drafts.pop(message.chat.id, None)
        logger.exception("group create failed")
        bot.send_message(message.chat.id, "Не удалось создать группу. Проверьте диагностику Pivas.", reply_markup=_hosts_keyboard())


def _group_edit_text(message, group_id, revision, action):
    if not _group_authorized(message.from_user, message.chat) or _group_cancel_step(message): return
    try:
        state = _group_catalog()
        if state["revision"] != revision:
            raise GroupMenuError("Группа изменилась после открытия. Откройте её заново.")
        group = _group_find(state, group_id)
        if not group: raise GroupMenuError("Группа больше не существует.")
        domains = list(group["domains"])
        if action == "rename":
            name = (message.text or "").strip()
            if not 1 <= len(name) <= 80 or any(ord(c) < 32 for c in name):
                _group_prompt(message.chat.id, "Название: от 1 до 80 символов.", _group_edit_text, group_id, revision, action)
                return
        else:
            values, error = _group_domains_from_message(message)
            if error or not values:
                _group_prompt(message.chat.id, error or "Пришлите домены текстом или .txt-файлом.", _group_edit_text, group_id, revision, action)
                return
            if action == "add": domains = sorted(set(domains) | set(values))
            elif action == "remove":
                values = [_group_domain_key(v) for v in values]
                missing = set(values) - set(domains)
                if missing: raise GroupMenuError("В группе нет домена: " + sorted(missing)[0])
                domains = sorted(set(domains) - set(values))
                if not domains: raise GroupMenuError("В группе должен остаться хотя бы один домен. Для полного удаления используйте «Удалить группу».")
            name = group["name"]
        result = _group_mutate({"revision": revision, "action": "update", "id": group_id, "name": name,
                                "slot": group["slot"], "domains": domains})
        _group_show(message.chat.id, result, group_id=group_id, notice="Группа обновлена.")
    except (GroupMenuError, UnicodeError) as exc:
        bot.send_message(message.chat.id, str(exc), reply_markup=_hosts_keyboard())
    except Exception:
        logger.exception("group text edit failed")
        bot.send_message(message.chat.id, "Не удалось изменить группу. Проверьте диагностику Pivas.", reply_markup=_hosts_keyboard())


@bot.callback_query_handler(func=lambda c: bool(c.data) and c.data.startswith("grp:"))
def group_callback(call):
    if not _group_authorized(call.from_user, call.message.chat):
        bot.answer_callback_query(call.id, "Доступ запрещён", show_alert=True)
        return
    try:
        parts = call.data.split(":")
        action = parts[1]
        if action == "noop":
            bot.answer_callback_query(call.id)
            return
        if action == "new":
            bot.answer_callback_query(call.id)
            _group_prompt(call.message.chat.id, "Название новой группы?", _group_create_name)
            return
        if action == "newslot" and len(parts) == 3 and parts[2] in ("1", "2"):
            draft = _group_drafts.get(call.message.chat.id)
            if not draft or draft["owner"] != call.from_user.id:
                raise GroupMenuError("Создание группы прервано. Начните заново.")
            draft["slot"] = int(parts[2])
            bot.answer_callback_query(call.id)
            _group_prompt(call.message.chat.id,
                          "Пришлите домены или IPv4-подсети по одному в строке, через пробел либо .txt-файлом. IPv6 пока не поддерживается.",
                          _group_create_domains)
            return
        state = _group_catalog()
        if action == "list" and len(parts) == 3:
            bot.answer_callback_query(call.id)
            _group_show(call.message.chat.id, state, int(parts[2]), call=call)
            return
        if action == "view" and len(parts) == 4:
            group = _group_find(state, parts[2])
            if not group: raise GroupMenuError("Группа больше не существует.")
            bot.answer_callback_query(call.id)
            _group_show(call.message.chat.id, state, int(parts[3]), group["id"], call=call)
            return
        if action not in ("add", "remove", "rename", "move", "toggle", "delete", "confirm"):
            raise GroupMenuError("Неизвестная кнопка. Откройте меню групп заново.")
        group_id = parts[2]
        group = _group_find(state, group_id)
        if not group: raise GroupMenuError("Группа больше не существует.")
        short_revision = parts[-1]
        _group_stale(state, short_revision)
        if action in ("add", "remove", "rename"):
            bot.answer_callback_query(call.id)
            prompt = {"add": "Пришлите домены или IPv4-подсети для добавления в группу. Можно .txt-файл.",
                      "remove": "Какие записи убрать из группы и VPN? Пришлите список или .txt-файл.",
                      "rename": "Новое название группы?"}[action]
            _group_prompt(call.message.chat.id, prompt, _group_edit_text, group_id, state["revision"], action)
            return
        if action == "delete":
            bot.answer_callback_query(call.id)
            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.row(_group_button("Да, удалить", f"grp:confirm:{group_id}:{short_revision}"),
                   _group_button("Отмена", f"grp:view:{group_id}:0"))
            bot.send_message(call.message.chat.id,
                             f"Удалить «{group['name']}» и убрать все {len(group['domains'])} доменов из VPN?",
                             reply_markup=kb)
            return
        if action == "move":
            if len(parts) != 5 or parts[3] not in ("1", "2"):
                raise GroupMenuError("Некорректный слот")
            request = {"action": "move", "slot": int(parts[3])}
        elif action == "toggle":
            request = {"action": "toggle", "enabled": not group["enabled"]}
        else:
            request = {"action": "delete"}
        bot.answer_callback_query(call.id, "Применяю…")
        request.update(revision=state["revision"], id=group_id)
        result = _group_mutate(request)
        _group_show(call.message.chat.id, result, group_id=None if action == "confirm" else group_id,
                    notice="Группа удалена." if action == "confirm" else "Готово.")
    except (GroupMenuError, ValueError, IndexError) as exc:
        try: bot.answer_callback_query(call.id, str(exc)[:180], show_alert=True)
        except Exception: pass
        try: bot.send_message(call.message.chat.id, str(exc)[:240])
        except Exception: pass
    except Exception:
        logger.exception("group_callback failed")
        try: bot.answer_callback_query(call.id, "Ошибка управления группой", show_alert=True)
        except Exception: pass


@bot.message_handler(regexp="Сервис", chat_types=["private"])
def service_message(message: types.Message):
    try:
        logger.info("User %s requested service menu", message.from_user.username)
        bot.send_message(
            message.chat.id,
            "Сервисное меню",
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in service_message: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.message_handler(regexp="Добавить пользователя", chat_types=["private"])
def add_admin_handler(message: types.Message):
    logger.info("User %s requested add new admin menu", message.from_user.username)
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    buttons = [
        types.KeyboardButton("Назад"),
    ]
    keyboard.add(*buttons)
    answer = bot.send_message(
        message.chat.id,
        f"Введите корректный id пользователя, которого нужно добавить как администратора\nНапример:\n{message.from_user.id}",
        reply_markup=keyboard,
    )
    bot.register_next_step_handler(answer, handle_add_new_admin)


def handle_add_new_admin(message: types.Message):
    if message.text == "Назад":
        service_message(message=message)
        return
    try:
        new_admin = int(message.text)
        admins = [new_admin]

        if os.path.isfile(CONFIG_PATH):
            config.read(CONFIG_PATH, encoding="UTF-8")
            existing_admins = config.get("ADMINS", "users_ids", fallback="")
            if existing_admins:
                admins.extend([int(a) for a in existing_admins.split(",") if a.strip().isdigit()])

        config['ADMINS'] = {
            'users_ids': ','.join(map(str, set(admins))),
        }

        with open(CONFIG_PATH, 'w', encoding="UTF-8") as config_file:
            config.write(config_file)

        bot.send_message(message.chat.id, f"Пользователь {new_admin} добавлен")
    except Exception as e:
        bot.send_message(message.chat.id, f"Ошибка: {str(e)}")
        add_admin_handler(message=message)


def _connections_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("Показать ссылки"),
        types.KeyboardButton("Проверить подключения"),
    )
    kb.add(
        types.KeyboardButton("Заменить ссылку-1"),
        types.KeyboardButton("Заменить ссылку-2"),
    )
    kb.add(types.KeyboardButton("⇄ Поменять ссылки"))
    kb.add(types.KeyboardButton("Названия слотов"))
    kb.add(types.KeyboardButton("Откат подключений"))
    kb.add(
        types.KeyboardButton("Список интерфейсов"),
        types.KeyboardButton("Смена интерфейса"),
    )
    kb.add(types.KeyboardButton("Назад"))
    return kb


@bot.message_handler(regexp="Управление подключениями", chat_types=["private"])
def connections_message(message: types.Message):
    try:
        logger.info(
            "User %s requested connection management", message.from_user.username
        )
        names = _slot_labels()
        bot.send_message(
            message.chat.id,
            "Управление подключениями.\n"
            f"{_slot_label(1, names)} — основной маршрут.\n"
            f"{_slot_label(2, names)} — второй маршрут и подключение бота.\n"
            "«⇄ Поменять ссылки» меняет подключения слотов местами одним нажатием.",
            reply_markup=_connections_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in connections_message: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.message_handler(regexp="Добавить хост", chat_types=["private"])
def add_host_prompt(message: types.Message):
    try:
        logger.info("User %s prompted to add host", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Пришлите домен(ы) для добавления: через пробел, запятую, по строке — или .txt-файлом со списком.",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_add_host)
    except Exception as e:
        logger.exception("Error in add_host_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.message_handler(regexp="Удалить хост", chat_types=["private"])
def delete_host_prompt(message: types.Message):
    try:
        logger.info("User %s prompted to delete host", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Введите домен(или несколько доменов, разделенных пробелом) для удаления:",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_delete_host)
    except Exception as e:
        logger.exception("Error in delete_host_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


TERMINAL_AUDIT_LOG = "/opt/etc/telegram4pivas/terminal_audit.log"

# Команды которые почти наверняка = ошибка/атака. Запрещаем полностью —
# если admin реально нужен rm -rf — пусть делает через SSH, где
# подтверждение осознанное.
TERMINAL_BLACKLIST = [
    r"\brm\s+-rf?\s+/(\s|$)",            # rm -rf /
    r"\brm\s+-rf?\s+/\*",                # rm -rf /*
    r"\brm\s+-rf?\s+/opt(\s|$|/\*)",     # rm -rf /opt (уничтожает Entware)
    r"\bmkfs\b",                         # форматирование
    r"\bdd\s+.*of=/dev/[sh]d",           # dd → диск
    r">\s*/dev/([sh]d|mmcblk|mtd)",      # запись в диск/flash
    r"\bchmod\s+-R\s+[0-7]*\s+/(\s|$)",  # chmod -R 777 / и т.п.
]


def _terminal_audit(user_id, username, cmd, exit_code):
    """Лог каждой команды терминала: кто, когда, что, результат."""
    try:
        import datetime
        ts = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
        line = f"[{ts}] user_id={user_id} user={username} rc={exit_code} cmd={cmd!r}\n"
        with open(TERMINAL_AUDIT_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.exception("terminal_audit failed: %s", e)


def _terminal_is_dangerous(cmd):
    for pat in TERMINAL_BLACKLIST:
        if re.search(pat, cmd):
            return pat
    return None


@bot.message_handler(regexp="Терминал", chat_types=["private"])
def custom_command_prompt(message: types.Message):
    try:
        logger.info("User %s entered terminal mode", message.from_user.username)
        user_states[message.chat.id] = True
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
        button = [types.KeyboardButton("Назад")]
        keyboard.add(*button)
        answer = bot.send_message(
            message.chat.id,
            "⚠️ *ROOT-ТЕРМИНАЛ*\n\n"
            "Команды выполняются под root. Каждая команда "
            f"записывается в аудит-лог:\n`{TERMINAL_AUDIT_LOG}`\n\n"
            "Запрещены: `rm -rf /`, `rm -rf /opt`, `mkfs`, `dd of=/dev/...`, "
            "запись в block-устройства, рекурсивный `chmod` на `/`.\n\n"
            "Выход: кнопка «Назад» или слово «Отмена».",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        bot.register_next_step_handler(answer, custom_command)
    except Exception as e:
        logger.exception("Error in custom_command_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def clean_string(text: str) -> str:
    # Было: text.replace("-", "") — это ломало любой вывод с дефисами
    # (VLESS flow "xtls-rprx-vision", slot-1, домены с дефисами "my-site.com"
    # и даже IDN punycode типа "xn--e1afmkfd"). Убрали, дефисы сохраняем.
    text = re.sub(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])', '', text)
    text = re.sub(r'[^\S\r\n]+', ' ', text).strip()
    return text


def send_long_message(output, message: types.Message):
    if len(output) > 4090:
        for x in range(0, len(output), 4090):
            bot.send_message(
                message.chat.id,
                mcode(output[x : x + 4090] + "\n"),
                parse_mode="MarkdownV2",
            )
            time.sleep(1)
    else:
        bot.send_message(
            message.chat.id,
            mcode(output),
            parse_mode="MarkdownV2",
        )


def scan_interfaces(param="Q"):
    try:
        logger.info("Scanning interfaces with parameter: %s", param)
        if param == "no_shadowsocks":
            command = [
                f'echo "Q" | pivas vpn set | grep -v "shadowsocks" | grep -v "Broadband connection" | grep -v "vless" | grep -v "Home network" | grep -E "В СЕТИ|ОТКЛЮЧЕН"'
            ]
        else:
            command = [f'echo "{param}" | pivas vpn set | grep -v "Broadband connection" | grep -v "Home network" | grep -v "vless" | grep -E "В СЕТИ|ОТКЛЮЧЕН"']
        with tempfile.TemporaryFile() as tempf:
            process = subprocess.Popen(command, shell=True, stdout=tempf)
            process.wait()
            tempf.seek(0)
            output = tempf.read().decode("utf-8")
            output_clean = clean_string(output)
        return output_clean
    except Exception as e:
        logger.exception("Error during interface scanning: %s", str(e))
        return "Ошибка при сканировании интерфейсов"


def make_keyboard_interfaces(list_interfaces):
    try:
        logger.debug("Creating keyboard for interfaces")

        list_interfaces_split = list_interfaces.splitlines()
        interface_next = []

        for line in list_interfaces_split:
            interface_name = line.strip(".")[0]
            interface_next.append(interface_name)

        keyboard_interfaces = types.InlineKeyboardMarkup()
        for interface_name in interface_next:
            keyboard_interfaces.add(
                types.InlineKeyboardButton(text=interface_name, callback_data=interface_name)
            )

        return keyboard_interfaces

    except Exception as e:
        logger.exception("Error in make_keyboard_interfaces: %s", str(e))
        return types.InlineKeyboardMarkup()


@bot.message_handler(regexp="Список интерфейсов", chat_types=["private"])
def handle_list_interfaces(message: types.Message):
    try:
        logger.info("User %s requested list of interfaces", message.from_user.username)
        bot.send_message(
            message.chat.id,
            "Производится сканирование интерфейсов",
        )

        bot.send_message(
            message.chat.id,
            mcode(scan_interfaces()),
            parse_mode="MarkdownV2",
        )
    except Exception as e:
        logger.exception("Error in handle_list_interfaces: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")

@bot.message_handler(regexp="^Диагностика$", chat_types=["private"])
def diag_report(message: types.Message):
    try:
        bot.send_message(message.chat.id, "Собираю диагностику, до 45 секунд. В отчёте будут домены и адреса серверов.")
        result = subprocess.run(
            ["/opt/bin/python3", "/opt/apps/pivas/bin/main/diagnostics.py"],
            capture_output=True, timeout=55,
        )
        if result.returncode:
            bot.send_message(message.chat.id, "Не удалось собрать отчёт: " + result.stderr.decode("utf-8", "replace")[:500])
            return
        report = io.BytesIO(result.stdout)
        report.name = "pivas-diag.txt"
        bot.send_document(message.chat.id, report, visible_file_name=report.name,
                          caption="Диагностика Pivas. Проверьте содержимое перед передачей другим людям.",
                          reply_markup=_service_keyboard())
    except Exception:
        logger.exception("Diagnostic report failed")
        bot.send_message(message.chat.id, "Не удалось собрать диагностику. Попробуйте позже.")


@bot.message_handler(regexp="Запросить лог", chat_types=["private"])
def log_request_handler(message: types.Message):
    logger.info("User %s requested log", message.from_user.username)
    answer = bot.send_message(
        message.chat.id,
        "Введите количество строк лога, которые необходимо прислать. Нумерация начинается с конца файла"
    )
    bot.register_next_step_handler(answer, handle_log_request)


def handle_log_request(message: types.Message):
    try:
        # Валидация: принимаем только число 1..10000.
        # Иначе злоумышленник (даже админ) мог бы передать '1; rm -rf /opt' —
        # shell=True + f-string передавал это прямо в sh.
        try:
            n = int((message.text or "").strip())
            if n < 1 or n > 10000:
                raise ValueError
        except ValueError:
            bot.send_message(
                message.chat.id,
                "Нужно число строк (1–10000). Попробуйте ещё раз — нажмите «Запросить лог».",
            )
            return

        # Читаем последние N строк напрямую средствами Python — без shell вообще.
        log_path = "/opt/etc/telegram4pivas/telegram4pivas_log.txt"
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            bot.send_message(message.chat.id, "Лог-файл не найден.")
            return
        log_message = clean_string("".join(lines[-n:]))
        send_long_message(log_message, message)
    except Exception as e:
        logger.exception("Error in log request: %s", str(e))
        bot.send_message(message.chat.id, f"Ошибка: {str(e)}")


@bot.message_handler(regexp="Смена интерфейса", chat_types=["private"])
def vpn_set_prompt(message: types.Message):
    try:
        logger.info("User %s prompted to change interface", message.from_user.username)
        bot.send_message(
            message.chat.id,
            "Производится сканирование интерфейсов:",
        )
        list_interfaces = scan_interfaces("no_shadowsocks")
        keyboard = make_keyboard_interfaces(list_interfaces)

        answer = bot.send_message(
            message.chat.id,
            mcode(list_interfaces),
            parse_mode="MarkdownV2",
            reply_markup=keyboard,
        )

        bot.register_next_step_handler(answer, handle_vpn_set)
    except Exception as e:
        logger.exception("Error in vpn_set_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.callback_query_handler(func=lambda call: call.data.isdigit())
def handle_vpn_set(call):
    try:
        interface_num = int(call.data)
        logger.info(
            "User %s selected interface %d", call.from_user.username, interface_num
        )

        bot.edit_message_reply_markup(
            call.message.chat.id, message_id=call.message.message_id, reply_markup=None
        )

        bot.send_message(
            call.message.chat.id,
            "Производится выбор интерфейса, ожидайте",
        )

        scan_interfaces(interface_num)


        bot.send_message(
            call.message.chat.id,
            "Готово",
        )

    except Exception as e:
        logger.exception("Error in handle_vpn_set: %s", str(e))
        bot.send_message(call.message.chat.id, "Произошла ошибка при смене интерфейса.")


def handle_add_host(message: types.Message):
    try:
        logger.info("User %s is adding host(s)", message.from_user.username)
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        buttons = [
            types.KeyboardButton("Добавить хост"),
            types.KeyboardButton("Удалить хост"),
            types.KeyboardButton("Список хостов"),
            types.KeyboardButton("Очистить список"),
            types.KeyboardButton("Импорт"),
            types.KeyboardButton("Экспорт"),
            types.KeyboardButton("Назад"),
        ]
        keyboard.add(*buttons)
        domains, err = _domains_from_message(message)
        if err or not domains:
            bot.send_message(message.chat.id, err or "Не вижу ни одного домена.", reply_markup=keyboard)
            return
        # один batch-вызов: dnsmasq обновляется один раз, а не на каждый домен
        with tempfile.TemporaryFile() as tempf:
            subprocess.Popen(["pivas", "vless", "1-add"] + domains, stdout=tempf, stderr=subprocess.STDOUT).wait()
            tempf.seek(0)
            output_clean = clean_string(tempf.read().decode("utf-8"))
        send_long_message(output_clean + "\n", message)
        bot.send_message(message.chat.id, "Добавление окончено", reply_markup=keyboard)
    except Exception as e:
        logger.exception("Error in handle_add_host: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при добавлении хоста.")


def handle_delete_host(message: types.Message):
    try:
        logger.info("User %s is deleting host(s)", message.from_user.username)
        keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        buttons = [
            types.KeyboardButton("Добавить хост"),
            types.KeyboardButton("Удалить хост"),
            types.KeyboardButton("Список хостов"),
            types.KeyboardButton("Очистить список"),
            types.KeyboardButton("Импорт"),
            types.KeyboardButton("Экспорт"),
            types.KeyboardButton("Назад"),
        ]
        keyboard.add(*buttons)
        domain_list = message.text.split()
        for domain in domain_list:
            with tempfile.TemporaryFile() as tempf:
                process = subprocess.Popen(["pivas", "del", domain], stdout=tempf)
                process.wait()
                tempf.seek(0)
                output = tempf.read().decode("utf-8")
                output_clean = clean_string(output)

                bot.send_message(
                    message.chat.id,
                    mcode(output_clean + "\n"),
                    parse_mode="MarkdownV2",
                    reply_markup=keyboard,
                )
                if len(domain_list) > 1 and domain == domain_list[-1]:
                    bot.send_message(
                    message.chat.id,
                    "Удаление окончено",
                    reply_markup=keyboard,
                )
    except Exception as e:
        logger.exception("Error in handle_delete_host: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при удалении хоста.")


@bot.message_handler(regexp="Список хостов", chat_types=["private"])
def list_hosts(message: types.Message):
    try:
        logger.info("User %s requested list of hosts", message.from_user.username)
        src = "/opt/etc/hosts.list" if os.path.exists("/opt/etc/hosts.list") else "/opt/etc/pivas.list"
        with open(src) as file:
            sites = file.readlines()

        if not sites:
            bot.send_message(message.chat.id, "Список пуст")
        else:
            sites.sort()
            response = "\r".join(sites)
            send_long_message(response, message)
    except Exception as e:
        logger.exception("Error in list_hosts: %s", str(e))
        bot.send_message(
            message.chat.id, "Произошла ошибка при получении списка хостов."
        )


@bot.message_handler(regexp="Очистить список", chat_types=["private"])
def clear_hosts(message: types.Message):
    try:
        logger.info(
            "User %s requested to clear the list of hosts", message.from_user.username
        )
        bot.send_message(
            message.chat.id,
            "Если вы уверены, что хотите удалить все хосты, отправьте /removeall",
        )
    except Exception as e:
        logger.exception("Error in clear_hosts: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.message_handler(commands=["removeall"], chat_types=["private"])
def remove_all_hosts(message: types.Message):
    try:
        logger.info("User %s is removing all hosts", message.from_user.username)
        with tempfile.TemporaryFile() as tempf:
            subprocess.Popen(['echo "Y" | pivas purge'], shell=True, stdout=tempf).wait()
            tempf.seek(0)
            output = clean_string(
                tempf.read()
                .decode("utf-8")
                .replace("Защищённый список будет очищен. Уверены?", "")
                .replace("Предыдущий защищённый список был сохранён в файл /opt/etc/.pivas/backup/pivas.list", "")
            )
            bot.send_message(
                message.chat.id, mcode("\n" + output + "\n"), parse_mode="MarkdownV2"
            )

        backup_file = InputFile("/opt/etc/.pivas/backup/pivas.list")
        bot.send_document(message.chat.id, backup_file)
    except Exception as e:
        logger.exception("Error in remove_all_hosts: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при удалении всех хостов.")


@bot.message_handler(regexp="Импорт", chat_types=["private"])
def import_prompt(message: types.Message):
    try:
        logger.info("User %s prompted to import hosts", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            f"Пришлите файл для импорта в формате pivas-списка: простой текст, по одному домену на строку, строки с '#' игнорируются. Поддерживаются короткие форматы: {mbold('PIVAS')}",
            parse_mode="MarkdownV2",
        )
        bot.register_next_step_handler(answer, handle_import)
    except Exception as e:
        logger.exception("Error in import_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def handle_import(message: types.Message):
    try:
        logger.info("User %s is importing hosts", message.from_user.username)
        file_info = bot.get_file(message.document.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        bot.send_message(
            message.chat.id,
            "Файл получен, ожидайте...",
        )
        src = "/opt/pivastelegram.import"
        with open(src, "wb") as file_import:
            file_import.write(downloaded_file)

        with tempfile.TemporaryFile() as tempf:
            subprocess.Popen(["pivas", "import", src], stdout=tempf).wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
            send_long_message(output, message)

        os.system(
            "awk 'NF > 0' /opt/etc/hosts.list > /opt/etc/hostsb.list && cp /opt/etc/hostsb.list /opt/etc/hosts.list && rm /opt/etc/hostsb.list"
        )
    except Exception as e:
        logger.exception("Error in handle_import: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при импорте хостов.")


@bot.message_handler(regexp="Экспорт", chat_types=["private"])
def export_hosts(message: types.Message):
    try:
        logger.info("User %s requested to export hosts", message.from_user.username)
        src = "/opt/etc/.pivas/backup/pivas_export.txt"
        result = subprocess.run(["pivas", "export", src], capture_output=True, text=True)

        if result.returncode != 0:
            logger.error("pivas export failed: %s", result.stderr)
            bot.send_message(message.chat.id, "Произошла ошибка при экспорте хостов.")
            return

        bot.send_document(message.chat.id, open(src, "rb"))
    except Exception as e:
        logger.exception("Error in export_hosts: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при экспорте хостов.")



@bot.message_handler(regexp="Перезагрузить роутер", chat_types=["private"])
def reboot_router(message: types.Message):
    try:
        logger.warning(
            "User %s requested to reboot the router", message.from_user.username
        )
        bot.send_message(message.chat.id, "Роутер перезагружается")
        logger.warning("Rebooting the router...")
        subprocess.Popen(["reboot"])
    except Exception as e:
        logger.exception("Error in reboot_router: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при перезагрузке роутера.")


@bot.message_handler(
    func=lambda message: message.chat.id in user_states
    and user_states[message.chat.id],
    chat_types=["private"],
)
def custom_command(message: types.Message):
    try:
        logger.info(
            "User %s executed command in terminal mode: %s",
            message.from_user.username,
            message.text,
        )
        interrupt_command = ["Отмена", "отмена", "Назад"]
        keyboard = _service_keyboard()
        if message.text in interrupt_command:
            user_states.pop(message.chat.id)
            bot.send_message(
                message.chat.id, "Вы вышли из режима терминала.", reply_markup=keyboard
            )
        else:
            cmd = message.text or ""
            # Блэклист: запрещаем заведомо-деструктивные команды
            bad = _terminal_is_dangerous(cmd)
            if bad:
                _terminal_audit(
                    message.from_user.id, message.from_user.username,
                    cmd, exit_code="BLOCKED"
                )
                bot.send_message(
                    message.chat.id,
                    f"⛔ Команда отклонена.\n"
                    f"Сработал защитный паттерн: `{bad}`\n\n"
                    f"Если ты точно знаешь что делаешь — подключись по SSH.",
                    parse_mode="Markdown",
                )
                return
            with tempfile.TemporaryFile() as tempf:
                output_proc = subprocess.Popen([cmd], shell=True, stdout=tempf, stderr=subprocess.STDOUT)
                output_proc.wait()
                tempf.seek(0)
                output = tempf.read().decode("utf-8", errors="replace")
            _terminal_audit(
                message.from_user.id, message.from_user.username,
                cmd, exit_code=output_proc.returncode
            )
            send_long_message(clean_string(output), message)
    except Exception as e:
        logger.exception("Error in custom_command: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при выполнении команды.")


@bot.message_handler(regexp="Запустить test", chat_types=["private"])
def run_test(message: types.Message):
    try:
        logger.info("User %s requested pivas status test", message.from_user.username)
        bot.send_message(message.chat.id, "Проверяю pivas…")
        parts = []
        for title, cmd, timeout in (
            ("pivas status", ["pivas", "status"], 10),
            ("pivas vless ping", ["pivas", "vless", "ping"], 25),
        ):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
                out = clean_string(((r.stdout or "") + (r.stderr or "")).strip())
                if not out:
                    out = f"(пустой вывод, код {r.returncode})"
                parts.append(f"{title}\n{out}")
            except subprocess.TimeoutExpired:
                parts.append(f"{title}\nтаймаут {timeout}с")
        send_long_message("\n\n".join(parts), message)
    except Exception as e:
        logger.exception("Error in run_test: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при запуске теста.")


@bot.message_handler(regexp="Запустить debug", chat_types=["private"])
def run_debug(message: types.Message):
    try:
        logger.info("User %s requested to run debug", message.from_user.username)
        bot.send_message(
            message.chat.id,
            f"Запущена команда {mcode('pivas debug')}",
            parse_mode="MarkdownV2",
        )
        src = "/opt/root/pivas.debug"
        process = subprocess.Popen(["pivas", "debug", src])
        process.wait()
        debug_file = InputFile(src)
        bot.send_document(message.chat.id, debug_file, parse_mode="MarkdownV2")
    except Exception as e:
        logger.exception("Error in run_debug: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при запуске debug.")


@bot.message_handler(regexp="^Остановить pivas$", chat_types=["private"])
def run_pivas_stop(message: types.Message):
    try:
        logger.info("User %s → pivas stop", message.from_user.username)
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "stop"],
            announce_text="Останавливаю pivas + xray…",
            settle_seconds=3,
        )
    except Exception as e:
        logger.exception("Error in pivas stop: %s", str(e))


@bot.message_handler(regexp="^Запустить pivas$", chat_types=["private"])
def run_pivas_start(message: types.Message):
    try:
        logger.info("User %s → pivas start", message.from_user.username)
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "start"],
            announce_text="Запускаю pivas + xray…",
            settle_seconds=5,
        )
    except Exception as e:
        logger.exception("Error in pivas start: %s", str(e))


@bot.message_handler(regexp="^Все домены$", chat_types=["private"])
def run_all_domains(message: types.Message):
    try:
        logger.info("User %s → all domains", message.from_user.username)
        with tempfile.TemporaryFile() as tempf:
            subprocess.Popen(["pivas", "show"], stdout=tempf, stderr=subprocess.STDOUT).wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
        bot.send_message(
            message.chat.id,
            mcode("\n" + output + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_hosts_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in all domains: %s", str(e))
        bot.send_message(message.chat.id, "Ошибка при получении списка.")


def _web_inline_kb(state):
    """state: 'on' (жив) или 'off' (нет). Кнопки: выкл/вкл + сменить пароль."""
    m = types.InlineKeyboardMarkup(row_width=2)
    if state == "on":
        m.add(
            types.InlineKeyboardButton("⏸ Выключить", callback_data="web:off"),
            types.InlineKeyboardButton("🔑 Сменить пароль", callback_data="web:passwd"),
        )
    else:
        m.add(
            types.InlineKeyboardButton("▶ Включить", callback_data="web:on"),
        )
    return m


def _web_send_status(chat_id):
    """Отправить карточку статуса веба с inline-кнопками."""
    if _web_is_running():
        url = _web_url()
        text = (
            f"🟢 Веб-интерфейс работает.\n\n"
            f"Откройте с Mac / iPhone / любого устройства в LAN:\n"
            f"   {url}\n\n"
            f"Логин: admin\n"
            f"(работает только внутри локальной сети, наружу не торчит)"
        )
    else:
        text = "🔴 Веб-интерфейс выключен."
    # Cначала — короткое сообщение, которое переключает нижнюю reply-клаву
    # на сервисное меню (чтобы «Отмена» от ввода пароля пропала).
    # НЕ удаляем его — Telegram привязывает клаву к сообщению, и delete
    # её убивает.
    try:
        bot.send_message(chat_id, "⚙️ Меню «Сервис»:", reply_markup=_service_keyboard())
    except Exception:
        pass
    # Затем — сам статус с inline-кнопками управления вебом.
    bot.send_message(chat_id, text, reply_markup=_web_inline_kb("on" if _web_is_running() else "off"))


def _web_start_interactive(chat_id, force_prompt_password=False):
    """Запускаем веб; если нет пароля — просим его у пользователя."""
    if not force_prompt_password:
        r = subprocess.run(["pivas", "web", "on"], capture_output=True, text=True, timeout=15)
        out_clean = clean_string((r.stdout + r.stderr).strip())
        if "Нет auth-конфига" not in out_clean and "setpass" not in out_clean:
            # Получилось либо уже запущен
            time.sleep(1)
            _web_send_status(chat_id)
            return
    # Нужен пароль
    answer = bot.send_message(
        chat_id,
        "Задайте пароль для веб-интерфейса (логин всегда admin).\n"
        "Пришлите его одним сообщением. Сообщение удалится автоматически.",
        reply_markup=_cancel_kb(),
    )
    bot.register_next_step_handler(answer, _handle_web_password)


def _handle_web_password(message: types.Message):
    try:
        if _is_cancel(message.text):
            bot.send_message(message.chat.id, "Отменено.", reply_markup=_service_keyboard())
            return
        pwd = (message.text or "").strip()
        # Удаляем сообщение с паролем (best effort)
        try:
            bot.delete_message(message.chat.id, message.message_id)
        except Exception:
            pass
        if len(pwd) < 4:
            answer = bot.send_message(
                message.chat.id,
                "Пароль слишком короткий (минимум 4 символа). Пришлите ещё раз.",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, _handle_web_password)
            return
        r = subprocess.run(
            ["/opt/etc/pivas-web/setpass", pwd],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode != 0:
            bot.send_message(
                message.chat.id,
                f"setpass не сработал:\n{r.stdout}\n{r.stderr}",
                reply_markup=_service_keyboard(),
            )
            return
        bot.send_message(message.chat.id, "✅ Пароль сохранён.")
        # Если веб работал — рестарт, чтоб подхватил новый пароль
        if _web_is_running():
            subprocess.run(["pivas", "web", "off"], capture_output=True, timeout=10)
            time.sleep(1)
        subprocess.run(["pivas", "web", "on"], capture_output=True, timeout=15)
        time.sleep(1)
        _web_send_status(message.chat.id)
    except Exception as e:
        logger.exception("_handle_web_password: %s", e)
        try:
            bot.send_message(message.chat.id, f"Ошибка: {e}", reply_markup=_service_keyboard())
        except Exception:
            pass


@bot.message_handler(regexp="^Веб$", chat_types=["private"])
def web_toggle(message: types.Message):
    try:
        if not WEB_FEATURE_ENABLED:
            bot.send_message(
                message.chat.id,
                "Web-UI не установлен в этой сборке бота.\n"
                "Чтобы включить веб — поставь pivas-web "
                "(install-pivas-web.sh) и бот-версию с web "
                "(install-bot-web.sh).",
            )
            return
        _web_send_status(message.chat.id)
    except Exception as e:
        logger.exception("web_toggle: %s", e)


@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("web:"))
def web_callback(call):
    try:
        if not WEB_FEATURE_ENABLED:
            bot.answer_callback_query(call.id, "Web-UI выключен в этой сборке")
            return
        action = call.data.split(":", 1)[1]
        if action == "on":
            bot.answer_callback_query(call.id, "Включаю…")
            _web_start_interactive(call.message.chat.id)
        elif action == "off":
            bot.answer_callback_query(call.id, "Выключаю…")
            subprocess.run(["pivas", "web", "off"], capture_output=True, timeout=10)
            time.sleep(1)
            _web_send_status(call.message.chat.id)
        elif action == "passwd":
            bot.answer_callback_query(call.id)
            _web_start_interactive(call.message.chat.id, force_prompt_password=True)
        else:
            bot.answer_callback_query(call.id, "Неизвестное действие")
    except Exception as e:
        logger.exception("web_callback: %s", e)
        try:
            bot.answer_callback_query(call.id, "Ошибка")
        except Exception:
            pass


@bot.message_handler(regexp="^Инфо о роутере$", chat_types=["private"])
def router_info(message: types.Message):
    """Показать статус и нагрузку: модель/FW, uptime, CPU load, RAM, disk /opt,
    состояние xray/pivas/dnsmasq/bot, exit-IP обоих slot'ов."""
    try:
        logger.info("User %s → router info", message.from_user.username)
        lines = []

        # ---- Keenetic модель / FW через RCI ----
        model, fw = "?", "?"
        try:
            import json as _json, urllib.request as _urlreq
            with _urlreq.urlopen("http://localhost:79/rci/show/version", timeout=3) as r:
                v = _json.loads(r.read().decode("utf-8", errors="replace"))
                model = v.get("title") or v.get("hw_type") or "?"
                fw = v.get("release") or v.get("title") or "?"
        except Exception:
            pass

        # ---- /proc штучки ----
        def _read(path, default=""):
            try:
                with open(path) as f:
                    return f.read()
            except Exception:
                return default

        up = _read("/proc/uptime").split()
        up_sec = int(float(up[0])) if up else 0
        up_days, up_rem = divmod(up_sec, 86400)
        up_hrs,  up_rem = divmod(up_rem, 3600)
        up_min,  _      = divmod(up_rem, 60)
        if up_days:
            uptime_str = f"{up_days}д {up_hrs}ч {up_min}мин"
        elif up_hrs:
            uptime_str = f"{up_hrs}ч {up_min}мин"
        else:
            uptime_str = f"{up_min}мин"

        load = _read("/proc/loadavg").split()
        load_str = f"{load[0]} / {load[1]} / {load[2]}" if len(load) >= 3 else "?"

        mem_total = mem_avail = 0
        for line in _read("/proc/meminfo").splitlines():
            if line.startswith("MemTotal:"):
                mem_total = int(line.split()[1])
            elif line.startswith("MemAvailable:") or (line.startswith("MemFree:") and not mem_avail):
                mem_avail = int(line.split()[1])
        mem_used_mb  = (mem_total - mem_avail) // 1024
        mem_total_mb = mem_total // 1024

        # ---- Disk /opt ----
        disk_line = ""
        try:
            r = subprocess.run(
                ["df", "-h", "/opt"], capture_output=True, text=True, timeout=3
            )
            for line in r.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 5:
                    disk_line = f"{parts[1]} (свободно {parts[3]}, {parts[4]} занято)"
                    break
        except Exception:
            pass

        # ---- Процессы ----
        def _pid_of(name):
            try:
                r = subprocess.run(["pidof", name], capture_output=True, text=True, timeout=2)
                return r.stdout.strip().split()[0] if r.stdout.strip() else None
            except Exception:
                return None

        def _rss_mb(pid):
            if not pid:
                return None
            status = _read(f"/proc/{pid}/status")
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
            return None

        xray_pid = _pid_of("xray")
        xray_line = "работает (PID {}, {} МБ)".format(xray_pid, _rss_mb(xray_pid)) if xray_pid else "ОСТАНОВЛЕН"

        dnsmasq_pid = _pid_of("dnsmasq")
        dnsmasq_line = f"работает (PID {dnsmasq_pid})" if dnsmasq_pid else "ОСТАНОВЛЕН"

        # Бот (процесс сам себя)
        try:
            bot_pid = os.getpid()
            bot_line = f"работает (PID {bot_pid}, {_rss_mb(bot_pid) or '?'} МБ)"
        except Exception:
            bot_line = "?"

        # ---- Slot exit-IP через быстрый curl ----
        def _exit_ip(proxy_arg):
            try:
                r = subprocess.run(
                    ["curl", "-s", "--max-time", "5", "-x", proxy_arg, "https://ifconfig.me"],
                    capture_output=True, text=True, timeout=7
                )
                ip = r.stdout.strip()
                return ip if len(ip) < 40 and ip else "?"
            except Exception:
                return "?"

        slot1_ip = _exit_ip("socks5h://127.0.0.1:1097")
        slot2_ip = _exit_ip("http://127.0.0.1:1098")

        # ---- slot-N addresses из pivas.json ----
        slot1_addr = slot2_addr = "?"
        try:
            import json as _json
            with open("/opt/etc/xray/pivas.json") as f:
                cfg = _json.load(f)
            for ob in cfg.get("outbounds", []):
                if ob.get("tag") == "slot-1":
                    slot1_addr = (ob.get("settings", {}) if ob.get("protocol") == "hysteria" else (ob.get("settings", {}).get("vnext") or [{}])[0]).get("address", "?")
                elif ob.get("tag") == "slot-2":
                    slot2_addr = (ob.get("settings", {}) if ob.get("protocol") == "hysteria" else (ob.get("settings", {}).get("vnext") or [{}])[0]).get("address", "?")
        except Exception:
            pass

        # ---- Наш форк pivas + bot: сколько жрёт ----
        # RSS всех xray-процессов (на случай зомби)
        xray_rss_total = 0
        xray_count = 0
        try:
            r = subprocess.run(["pidof", "xray"], capture_output=True, text=True, timeout=2)
            for pid in r.stdout.split():
                rss = _rss_mb(pid)
                if rss:
                    xray_rss_total += rss
                    xray_count += 1
        except Exception:
            pass

        # RSS бота и связанных процессов (python3 = наш бот)
        bot_rss = _rss_mb(os.getpid()) or 0
        dnscrypt_pid = _pid_of("dnscrypt-proxy")
        dnscrypt_rss = _rss_mb(dnscrypt_pid) or 0

        total_project_rss = xray_rss_total + bot_rss + dnscrypt_rss

        # Диск: наши артефакты
        def _du_kb(path):
            try:
                r = subprocess.run(["du", "-sk", path], capture_output=True, text=True, timeout=3)
                return int(r.stdout.split()[0]) if r.stdout else 0
            except Exception:
                return 0
        disk_kb = (
            _du_kb("/opt/apps/pivas")
            + _du_kb("/opt/etc/telegram4pivas")
            + _du_kb("/opt/sbin/xray")
        )
        log_kb = _du_kb("/tmp/log/xray-access.log") + _du_kb("/tmp/log/xray-errors.log") \
               + _du_kb("/opt/etc/telegram4pivas/telegram4pivas_log.txt")

        # pivas-список
        try:
            with open("/opt/etc/pivas.list") as f:
                pivas_n = sum(1 for line in f if line.strip() and not line.startswith("#"))
        except Exception:
            pivas_n = 0

        # ---- Более человечная сборка ----
        mem_free_mb = mem_total_mb - mem_used_mb
        mem_pct = int(mem_used_mb * 100 / mem_total_mb) if mem_total_mb else 0

        # Disk: пытаемся вычленить числа
        disk_total = disk_used = disk_free = "?"
        try:
            r = subprocess.run(
                ["df", "-h", "/opt"], capture_output=True, text=True, timeout=3
            )
            for line in r.stdout.splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 5:
                    disk_total = parts[1]
                    disk_used  = parts[2]
                    disk_free  = parts[3]
                    disk_pct   = parts[4]
                    break
        except Exception:
            disk_pct = "?"

        load1 = load[0] if load else "?"
        try:
            ncpu = os.cpu_count() or 1
            # loadavg считается на все ядра: нормируем на их число,
            # иначе 2.0 на 4-ядерном роутере выглядит как перегруз
            lval = float(load1) / ncpu
            if lval < 0.35:   load_hint = "низкая"
            elif lval < 0.7:  load_hint = "средняя"
            elif lval < 1.0:  load_hint = "высокая"
            else:             load_hint = "КРИТИЧНАЯ"
            load_hint = f"{load_hint}, {int(lval * 100)} % от {ncpu} ядер"
        except Exception:
            load_hint = "?"

        # Uptime — убираем минуты если дней > 0
        if up_days:
            up_human = f"{up_days} дн. {up_hrs} ч."
        elif up_hrs:
            up_human = f"{up_hrs} ч. {up_min} мин."
        else:
            up_human = f"{up_min} мин."

        # Процент RAM проекта от общей
        proj_pct = int(total_project_rss * 100 / mem_total_mb) if mem_total_mb else 0

        lines.append(f"Роутер:   {model} (FW {fw})")
        lines.append(f"Работает: {up_human}")
        lines.append(f"Нагрузка CPU: {load1}  ({load_hint})")
        lines.append("")
        lines.append(f"Память роутера")
        lines.append(f"  всего:     {mem_total_mb} МБ")
        lines.append(f"  занято:    {mem_used_mb} МБ ({mem_pct} %)")
        lines.append(f"  свободно:  {mem_free_mb} МБ")
        lines.append("")
        lines.append(f"Диск /opt (USB)")
        lines.append(f"  всего:     {disk_total}")
        lines.append(f"  занято:    {disk_used} ({disk_pct})")
        lines.append(f"  свободно:  {disk_free}")
        lines.append("")
        lines.append("Статус служб")
        lines.append(f"  xray:     {'работает' if xray_count else 'ОСТАНОВЛЕН'}" +
                     (f"  ⚠️ {xray_count} процессов (должен быть 1)" if xray_count > 1 else ""))
        lines.append(f"  dnsmasq:  {'работает' if dnsmasq_pid else 'ОСТАНОВЛЕН'}")
        lines.append(f"  бот:      работает (v{BOT_VERSION})")
        lines.append("")
        lines.append("VPN-каналы")
        lines.append(f"  slot-1 (основной)   {slot1_addr} → {slot1_ip}")
        lines.append(f"  slot-2 (для бота)   {slot2_addr} → {slot2_ip}")
        lines.append(f"  доменов в списке:   {pivas_n}")
        lines.append("")
        lines.append("Сколько занимает наш проект")
        lines.append(f"  в памяти:   {total_project_rss} МБ ({proj_pct} % от {mem_total_mb} МБ)")
        lines.append(f"     xray       {xray_rss_total} МБ" + (f"  ({xray_count} процесса)" if xray_count > 1 else ""))
        lines.append(f"     бот        {bot_rss} МБ")
        lines.append(f"     dnscrypt   {dnscrypt_rss} МБ")
        lines.append(f"  на диске:   {disk_kb//1024} МБ (логи {log_kb} КБ)")
        if xray_count > 1:
            lines.append("")
            lines.append("⚠️  Обнаружены зомби-процессы xray.")
            lines.append("    Нажмите «Остановить pivas» → «Запустить pivas» —")
            lines.append("    освободится примерно " + str((xray_count - 1) * 11) + " МБ RAM.")

        bot.send_message(
            message.chat.id,
            mcode("\n" + "\n".join(lines) + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in router_info: %s", str(e))
        try:
            bot.send_message(message.chat.id, "Ошибка при сборе инфо о роутере.")
        except Exception:
            pass


@bot.message_handler(regexp="^Обновить пакет$", chat_types=["private"])
def pkg_upload_prompt(message: types.Message):
    try:
        if not _group_authorized(message.from_user, message.chat): return
        logger.info("User %s → pkg upload prompt", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Пришлите .ipk файл одним сообщением (как документ).\n"
            "Поддерживаются: pivas, pivas-web, telegram4pivas, xray, xray-core, pivas-quic-probe; "
            "pivas-full — только поверх установленного pivas-full.\n"
            "Размер — до 20 МБ (лимит загрузки в Pivas).\n"
            "После установки прежнее состояние Pivas и веба восстановится. "
            "Бот может перезапуститься; результат придёт отдельным сообщением.",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_pkg_upload)
    except Exception as e:
        logger.exception("Error in pkg_upload_prompt: %s", str(e))


def _extract_pkg_meta(ipk_path):
    """Извлечь из control внутри .ipk: (Package, Architecture, Version).
    Возвращает кортеж с None-ами при ошибке парсинга."""
    try:
        import tarfile
        with tarfile.open(ipk_path, "r:gz") as outer:
            ctl_member = None
            for m in outer.getmembers():
                if m.name.endswith("control.tar.gz"):
                    ctl_member = m
                    break
            if not ctl_member:
                return (None, None, None)
            with tarfile.open(fileobj=outer.extractfile(ctl_member), mode="r:gz") as inner:
                for m in inner.getmembers():
                    if m.name.endswith("control") and not m.name.endswith(".tar.gz"):
                        data = inner.extractfile(m).read().decode("utf-8", "replace")
                        out = {"Package": None, "Architecture": None, "Version": None}
                        for line in data.splitlines():
                            for k in out.keys():
                                if line.startswith(k + ":"):
                                    out[k] = line.split(":", 1)[1].strip()
                        return (out["Package"], out["Architecture"], out["Version"])
    except Exception as e:
        logger.warning("extract_pkg_meta failed: %s", e)
    return (None, None, None)


def _router_arches():
    """Архитектуры роутера (из 'opkg print-architecture')."""
    try:
        r = subprocess.run(
            ["opkg", "print-architecture"],
            capture_output=True, text=True, timeout=5,
        )
        arches = set()
        for line in r.stdout.splitlines():
            p = line.split()
            if len(p) >= 2 and p[0] == "arch":
                arches.add(p[1])
        return arches
    except Exception as e:
        logger.warning("_router_arches failed: %s", e)
        return set()


def _package_installed(name):
    try:
        result = subprocess.run(["opkg", "list-installed", name], capture_output=True, text=True, timeout=5)
        return result.returncode == 0 and any(line.startswith(name + " - ") for line in result.stdout.splitlines())
    except (OSError, subprocess.TimeoutExpired):
        return False


def handle_pkg_upload(message: types.Message):
    ipk_path = None
    worker_path = None
    try:
        if not _group_authorized(message.from_user, message.chat): return
        if _is_cancel(message.text if not getattr(message, 'document', None) else ''):
            bot.send_message(message.chat.id, 'Отменено.', reply_markup=_service_keyboard())
            return
        doc = getattr(message, "document", None)
        if not doc:
            answer = bot.send_message(
                message.chat.id,
                "Не вижу файла. Пришлите .ipk ещё раз (как документ).",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, handle_pkg_upload)
            return

        fname = doc.file_name or "upload.ipk"
        if not fname.lower().endswith(".ipk"):
            bot.send_message(
                message.chat.id,
                f"Ожидаю файл с расширением .ipk, получил: {fname}",
                reply_markup=_service_keyboard(),
            )
            return

        if doc.file_size and doc.file_size > 20 * 1024 * 1024:
            bot.send_message(
                message.chat.id,
                f"Файл слишком большой: {doc.file_size // 1024 // 1024} МБ. "
                "Лимит загрузки в Pivas — 20 МБ.",
                reply_markup=_service_keyboard(),
            )
            return

        # Уникальный файл на Entware-носителе не перезапишет параллельную
        # установку и не расходует RAM /tmp для больших full-пакетов.
        os.makedirs('/opt/tmp', exist_ok=True)
        bot.send_message(message.chat.id, f"Скачиваю {fname} ({doc.file_size or '?'} байт)…")

        file_info = bot.get_file(doc.file_id)
        data = bot.download_file(file_info.file_path)
        if len(data) > 20 * 1024 * 1024:
            bot.send_message(message.chat.id, 'Пакет больше 20 МБ; установка отменена.', reply_markup=_service_keyboard())
            return
        fd, ipk_path = tempfile.mkstemp(prefix='pivas-upload-', suffix='.ipk', dir='/opt/tmp')
        with os.fdopen(fd, "wb") as f:
            f.write(data)

        # Идентификация пакета
        pkg, arch, ver = _extract_pkg_meta(ipk_path)
        if not pkg or not arch or not ver:
            bot.send_message(
                message.chat.id,
                "Не удалось прочитать метаданные пакета из .ipk. Отмена.",
                reply_markup=_service_keyboard(),
            )
            try: os.unlink(ipk_path)
            except Exception: pass
            ipk_path = None
            return

        logger.info("pkg_upload: file=%s pkg=%s arch=%s ver=%s size=%s",
                    fname, pkg, arch, ver, doc.file_size)

        # Whitelist
        allowed = {"pivas", "pivas-web", "pivas-full", "telegram4pivas", "xray", "xray-core", "pivas-quic-probe"}
        if pkg not in allowed:
            bot.send_message(
                message.chat.id,
                f"Пакет '{pkg}' не в списке разрешённых.\n"
                f"Разрешены: {', '.join(sorted(allowed))}.",
                reply_markup=_service_keyboard(),
            )
            try: os.unlink(ipk_path)
            except Exception: pass
            ipk_path = None
            return

        # Modular packages must not replace pieces owned by pivas-full.
        full_installed = _package_installed('pivas-full')
        if (full_installed and pkg != 'pivas-full') or (not full_installed and pkg == 'pivas-full'):
            bot.send_message(
                message.chat.id,
                "Тип пакета не совпадает с установленным вариантом Pivas. "
                "Для модульной установки пришлите отдельный .ipk; для pivas-full — только pivas-full. "
                "Переход между вариантами требует SSH.",
                reply_markup=_service_keyboard(),
            )
            os.unlink(ipk_path)
            ipk_path = None
            return

        # Архитектурная совместимость
        router_arches = _router_arches()
        if arch and router_arches and arch not in router_arches:
            bot.send_message(
                message.chat.id,
                f"Архитектура пакета '{arch}' не совпадает с роутером.\n"
                f"Роутер поддерживает: {', '.join(sorted(router_arches)) or '?'}.\n"
                f"Для всех-арх пакетов должно быть Architecture: all.",
                reply_markup=_service_keyboard(),
            )
            try: os.unlink(ipk_path)
            except Exception: pass
            ipk_path = None
            return

        # Worker survives replacement of the bot itself and restores the
        # pre-update Pivas/web state after packages whose postinst pauses Pivas.
        worker_fd, worker_path = tempfile.mkstemp(prefix='pivas-update-worker-', suffix='.py', dir='/opt/tmp')
        with os.fdopen(worker_fd, 'wb') as destination, open('/opt/etc/telegram4pivas/update_worker.py', 'rb') as source:
            shutil.copyfileobj(source, destination)
        os.chmod(worker_path, 0o700)
        active = not os.path.exists('/opt/etc/pivas.paused')
        web_enabled = os.path.exists('/opt/etc/pivas-web/enabled')
        bot.send_message(message.chat.id,
                         f"Принял {pkg} {ver}. Установка начнётся через 3 секунды. "
                         "После неё пришлю результат; если бот перезапустится, проверьте «Статус обновления».",
                         reply_markup=_service_keyboard())
        subprocess.Popen(
            ['/opt/bin/python3', worker_path, ipk_path, pkg, ver, str(message.chat.id),
             '1' if active else '0', '1' if web_enabled else '0'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        ipk_path = None
        worker_path = None
    except Exception as e:
        logger.exception("Error in handle_pkg_upload: %s", str(e))
        for path in (ipk_path, worker_path):
            if path:
                try: os.unlink(path)
                except OSError: pass
        try:
            bot.send_message(message.chat.id, "Не удалось подготовить пакет к установке. Проверьте лог бота.")
        except Exception:
            pass


@bot.message_handler(regexp="^Статус обновления$", chat_types=["private"])
def pkg_update_status(message: types.Message):
    if not _group_authorized(message.from_user, message.chat): return
    try:
        with open('/opt/tmp/pivas-update-status.json', encoding='utf-8') as stream:
            status = json.load(stream)
        state = 'успешно' if status.get('ok') else 'ошибка'
        details = '; '.join(status.get('errors', []))
        bot.send_message(message.chat.id,
                         f"Последнее обновление: {status.get('package', '?')} "
                         f"{status.get('version', '?')} — {state}." +
                         (f" {details}" if details else ''),
                         reply_markup=_service_keyboard())
    except (OSError, ValueError):
        bot.send_message(message.chat.id, 'Завершённых обновлений пока нет.', reply_markup=_service_keyboard())


@bot.message_handler(regexp="Запустить reset", chat_types=["private"])
def run_reset(message: types.Message):
    try:
        logger.info("User %s requested to run reset", message.from_user.username)
        bot.send_message(
            message.chat.id,
            f"Запущена команда {mcode('pivas reset')}",
            parse_mode="MarkdownV2",
        )
        with tempfile.TemporaryFile() as tempf:
            reset_proc = subprocess.Popen(["pivas", "reset"], stdout=tempf)
            reset_proc.wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
            bot.send_message(
                message.chat.id,
                mcode("\n" + output + "\n"),
                parse_mode="MarkdownV2",
            )
    except Exception as e:
        logger.exception("Error in run_reset: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при запуске сброса.")


def _web_is_running():
    try:
        with open("/tmp/run/pivas-web.pid") as f:
            pid = f.read().strip()
        if pid and os.path.isdir(f"/proc/{pid}"):
            return True
    except Exception:
        pass
    return False


def _web_url():
    """URL веб-интерфейса (bind br0, fallback 192.168.1.1)."""
    try:
        with open("/proc/net/fib_trie") as f:
            pass  # just to avoid static analysis
    except Exception:
        pass
    try:
        r = subprocess.run(
            ["ip", "-4", "addr", "show", "br0"],
            capture_output=True, text=True, timeout=2
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("inet "):
                ip = line.split()[1].split("/")[0]
                return f"http://{ip}:8888/"
    except Exception:
        pass
    return "http://192.168.1.1:8888/"


def _service_keyboard():
    # Сервис — диагностика/управление службой, НЕ vless и НЕ хосты.
    # Кнопка веба динамическая — показывает актуальное действие.
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton("Остановить pivas"),
        types.KeyboardButton("Запустить pivas"),
    )
    kb.add(
        types.KeyboardButton("Запустить test"),
        types.KeyboardButton("Запустить debug"),
    )
    kb.add(
        types.KeyboardButton("Запустить reset"),
        types.KeyboardButton("Перезагрузить роутер"),
    )
    kb.add(
        types.KeyboardButton("Терминал"),
        types.KeyboardButton("Запросить лог"),
    )
    kb.add(types.KeyboardButton("Диагностика"))
    kb.add(
        types.KeyboardButton("Инфо о роутере"),
        types.KeyboardButton("Обновить пакет"),
    )
    kb.add(types.KeyboardButton("Статус обновления"))
    # Кнопка «Веб» показывается только если установлен pivas-web.
    # Определяется либо флагом /opt/etc/telegram4pivas/.flavor=web, либо
    # автодетектом по наличию /opt/etc/pivas-web/server.py.
    if WEB_FEATURE_ENABLED:
        kb.add(types.KeyboardButton("Веб"))
    kb.add(types.KeyboardButton("Добавить пользователя"))
    kb.add(types.KeyboardButton("Назад"))
    return kb


@bot.message_handler(regexp=r"^(?:Показать VLESS|Показать ссылки)$", chat_types=["private"])
def vless_show(message: types.Message):
    try:
        logger.info("User %s requested vless show", message.from_user.username)
        with tempfile.TemporaryFile() as tempf:
            proc = subprocess.Popen(["pivas", "vless", "show"], stdout=tempf, stderr=subprocess.STDOUT)
            proc.wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
        _send_connection_output(message.chat.id, output)
    except Exception as e:
        logger.exception("Error in vless_show: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка при получении ссылок.")


def _send_connection_output(chat_id, output):
    if len(output) <= 3500:
        bot.send_message(chat_id, mcode("\n"+output+"\n"), parse_mode="MarkdownV2", reply_markup=_connections_keyboard())
        return
    slot=0;links=[]
    for line in output.splitlines():
        match=re.match(r'^Slot ([12]):',line.strip())
        if match:slot=int(match.group(1))
        if line.strip().startswith('URL:'):
            link=line.strip()[4:].strip()
            if link.startswith(('vless://','hysteria2://','hy2://')):links.append((slot or len(links)+1,link))
    for number,link in links:
        with io.BytesIO((link+'\n').encode()) as document:
            bot.send_document(chat_id,document,visible_file_name='pivas-slot-'+str(number)+'.txt')
    bot.send_message(chat_id,'Длинные ссылки отправлены файлами. Их можно сохранить или прислать при настройке слота.',reply_markup=_connections_keyboard())


@bot.message_handler(regexp=r"^(?:Проверить VLESS|Проверить подключения)$", chat_types=["private"])
def vless_ping(message: types.Message):
    try:
        logger.info("User %s requested vless ping", message.from_user.username)
        wait = bot.send_message(message.chat.id, "🏓 Пингую slot-1 и slot-2…")
        with tempfile.TemporaryFile() as tempf:
            proc = subprocess.Popen(
                ["pivas", "vless", "ping"],
                stdout=tempf, stderr=subprocess.STDOUT,
            )
            proc.wait(timeout=20)
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8", errors="replace"))
        try:
            bot.delete_message(message.chat.id, wait.message_id)
        except Exception:
            pass
        bot.send_message(
            message.chat.id,
            mcode("\n" + output + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_connections_keyboard(),
        )
    except subprocess.TimeoutExpired:
        bot.send_message(message.chat.id, "Пинг не завершился за 20 секунд.")
    except Exception as e:
        logger.exception("Error in vless_ping: %s", str(e))
        bot.send_message(message.chat.id, f"Ошибка пинга: {e}")




def _send_with_retry(chat_id, text, **kwargs):
    # После `pivas vless set/2-add/2-del` xray ребутится ~2 сек, наш HTTP-прокси
    # отваливается с ProxyError/ConnectionRefused. Держим ретраи, пока
    # xray не поднимется обратно.
    last_exc = None
    for attempt in range(6):
        try:
            return bot.send_message(chat_id, text, **kwargs)
        except Exception as e:
            last_exc = e
            logger.warning("send_message attempt %d failed: %s", attempt + 1, e)
            time.sleep(2)
    raise last_exc


@bot.message_handler(regexp=r"^⇄ Поменять ссылки$", chat_types=["private"])
def vless_swap(message: types.Message):
    chat_id = message.chat.id
    if not _vless_swap_lock.acquire(blocking=False):
        try:
            _send_with_retry(chat_id, "Обмен ссылками уже выполняется. Дождитесь результата.")
        except Exception:
            logger.warning("Could not deliver swap busy message")
        return
    try:
        try:
            bot.send_message(chat_id, "Меняю ссылки слотов 1 ⇄ 2. Группы остаются в своих слотах. Связь с ботом может ненадолго прерваться.")
        except Exception:
            logger.warning("Could not announce slot swap")
        # Run once. Retrying Telegram delivery must never swap the links again.
        try:
            result = subprocess.run(["pivas", "vless", "swap"], capture_output=True, text=True)
            if result.returncode:
                text = "Не удалось поменять ссылки. Проверьте, что оба слота настроены, и посмотрите диагностический отчёт."
            else:
                changed = json.loads(result.stdout)["changed"]
                if type(changed) is not bool:
                    raise ValueError("Invalid swap result")
                text = ("Ссылки слотов 1 ⇄ 2 поменялись местами. Группы и домены остались в своих слотах."
                        if changed else "В обоих слотах уже одинаковые ссылки. Перезапуск не потребовался.")
        except Exception:
            # CLI output can contain connection credentials; do not forward it.
            logger.warning("Slot swap command did not return a valid result")
            text = "Не удалось получить результат обмена. Проверьте состояние слотов перед повторным нажатием."
        time.sleep(3)
        try:
            _send_with_retry(chat_id, text, reply_markup=_connections_keyboard())
        except Exception:
            logger.warning("Slot swap finished, but the result could not be delivered")
    finally:
        _vless_swap_lock.release()


def _run_pivas_and_reply(chat_id, argv, announce_text=None, settle_seconds=3):
    # Унифицированная схема «pivas что-то делает с xray»: сначала announce
    # (пока HTTP-прокси ещё жив), потом subprocess, sleep, ответ с retry.
    if announce_text:
        try:
            bot.send_message(chat_id, announce_text)
        except Exception as e:
            logger.warning("announce send failed: %s", e)
    with tempfile.TemporaryFile() as tempf:
        proc = subprocess.Popen(argv, stdout=tempf, stderr=subprocess.STDOUT)
        proc.wait()
        tempf.seek(0)
        output = clean_string(tempf.read().decode("utf-8"))
    if settle_seconds:
        time.sleep(settle_seconds)
    # Batch-добавление сотен доменов даёт вывод длиннее лимита Telegram (4096):
    # режем по строкам, клавиатуру цепляем к последнему куску.
    chunks, cur = [], ""
    for line in output.splitlines():
        if len(cur) + len(line) + 1 > 3800 and cur:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur or output)
    for i, ch in enumerate(chunks):
        last = i == len(chunks) - 1
        _send_with_retry(
            chat_id,
            mcode("\n" + ch + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_service_keyboard() if last else None,
        )
        if not last:
            time.sleep(1)


def _vless_set_prompt(message: types.Message, slot: int):
    try:
        logger.info("User %s prompted vless set-%d", message.from_user.username, slot)
        answer = bot.send_message(
            message.chat.id,
            f"Пришлите ссылку VLESS или Hysteria 2 для {_slot_label(slot)} сообщением либо одним .txt-файлом.\n"
            "Поддерживаются VLESS TCP + Reality, gRPC + TLS, XHTTP + TLS/Reality (auto, packet-up, stream-up, stream-one) и Hysteria 2 (hysteria2://, hy2://), включая Salamander. Частный TLS-сертификат привязывается по отпечатку при первом сохранении ссылки.",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, _handle_vless_set, slot)
    except Exception as e:
        logger.exception("Error in _vless_set_prompt (slot=%d): %s", slot, str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def _connection_from_message(message):
    document=getattr(message,'document',None)
    if document:
        if not (document.file_name or '').lower().endswith('.txt') or (document.file_size or 0)>32768:
            raise ValueError('Пришлите .txt-файл до 32 КБ с одной ссылкой.')
        try:
            data=bot.download_file(bot.get_file(document.file_id).file_path)
            if len(data)>32768:raise ValueError()
            text=data.decode('utf-8-sig')
        except Exception:
            raise ValueError('Не удалось прочитать ссылку: нужен UTF-8 .txt-файл до 32 КБ.') from None
    else:
        text=message.text or ''
    url=text.strip()
    if len(url)>16384 or '\n' in url or '\r' in url:
        raise ValueError('Нужна одна ссылка длиной до 16 КБ.')
    return url


def _handle_vless_set(message: types.Message, slot: int):
    try:
        if _is_cancel(message.text if not getattr(message, 'document', None) else ''):
            bot.send_message(message.chat.id, 'Отменено.', reply_markup=_connections_keyboard())
            return
        try:
            url = _connection_from_message(message)
        except ValueError as error:
            answer=bot.send_message(message.chat.id,str(error),reply_markup=_cancel_kb())
            bot.register_next_step_handler(answer,_handle_vless_set,slot)
            return
        logger.info("User %s submitted vless url for slot-%d (len=%d)",
                    message.from_user.username, slot, len(url))
        if not url.startswith(("vless://", "hysteria2://", "hy2://")):
            # Не съедаем следующий ход — перерегистрируем, чтобы пользователь
            # мог тут же прислать корректную ссылку.
            answer = bot.send_message(
                message.chat.id,
                "Ссылка должна начинаться с vless://, hysteria2:// или hy2:// — пришлите корректную ссылку одним сообщением.",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, _handle_vless_set, slot)
            return
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "vless", f"set-{slot}", url],
            announce_text=f"Применяю ссылку в slot-{slot}, xray перезапускается…",
        )
    except Exception as e:
        logger.exception("Error in _handle_vless_set (slot=%d): %s", slot, str(e))
        try:
            bot.send_message(message.chat.id, "Произошла ошибка при настройке слота.")
        except Exception:
            pass


@bot.message_handler(regexp=r"^Заменить (?:VLESS|ссылку)\-1$", chat_types=["private"])
def vless_set1_prompt(message: types.Message):
    _vless_set_prompt(message, 1)


@bot.message_handler(regexp=r"^Заменить (?:VLESS|ссылку)\-2$", chat_types=["private"])
def vless_set2_prompt(message: types.Message):
    _vless_set_prompt(message, 2)


@bot.message_handler(regexp=r"^Slot\-1: \+домен$", chat_types=["private"])
def vless_slot1_add_prompt(message: types.Message):
    try:
        logger.info("User %s prompted slot-1 +domain", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Пришлите домен(ы) для slot-1 (основной список pivas).\n"
            "Можно сразу много: через пробел, запятую, по одному на строку — или .txt-файлом со списком.",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_slot1_add)
    except Exception as e:
        logger.exception("Error in vless_slot1_add_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def _cancel_kb():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add(types.KeyboardButton("Отмена"))
    return kb


def _is_cancel(text):
    t = (text or "").strip().lower()
    return t in ("отмена", "назад", "cancel", "/cancel")


# Лейблы reply-клавиатур верхнего уровня. Если юзер в состоянии
# next_step_handler (ждём домены/пароль/ввод) тапает одну из этих
# кнопок — это не ответ на промпт, а попытка перейти в другое меню.
# Используется в обработчиках чтобы досрочно выйти из висящего шага
# и отдать сообщение в обычный message_handler chain.
_MENU_BUTTONS = frozenset((
    "клиенты", "сервис", "хосты", "назад",
    "показать vless", "пинг vless",
    "⇄ поменять ссылки", "названия слотов",
    "запустить pivas", "остановить pivas",
    "запустить test", "запустить debug", "запустить reset",
    "перезагрузить роутер", "терминал", "запросить лог", "диагностика",
    "инфо о роутере", "обновить пакет", "статус обновления", "веб",
    "добавить пользователя",
    "список", "очистить", "импорт", "экспорт", "все домены", "группы доменов",
    "управление хостами", "управление подключениями",
))


def _looks_like_menu_nav(text):
    return (text or "").strip().lower() in _MENU_BUTTONS


def _parse_domains(text):
    """Из текста вытаскиваем список доменов: split по пробелам/запятым/строкам,
    комментарии после # отбрасываем, чистим схему/путь/www, дедуп.
    pivas сам валидирует формат домена."""
    import re as _re
    seen = set()
    out = []
    for line in (text or '').splitlines():
        line = _re.sub(r'#.*$', '', line)
        for tok in _re.split(r'[\s,;]+', line.strip()):
            if not tok:
                continue
            d = tok.strip().lower()
            d = _re.sub(r'^https?://', '', d)
            d = d.split('/', 1)[0]
            d = _re.sub(r'^www\.', '', d)
            d = _re.sub(r'^\*?\.', '', d)
            d = d.rstrip('.,;')
            if not d or '.' not in d:
                continue
            if d not in seen:
                seen.add(d)
                out.append(d)
    return out


_DOMAIN_FILE_MAX = 512 * 1024


def _domains_from_message(message: types.Message):
    """Домены из сообщения: текст/подпись и/или приложенный .txt-файл.
    Возвращает (domains, error_text)."""
    text = (message.text or "") + "\n" + (message.caption or "")
    doc = getattr(message, "document", None)
    if doc:
        fname = doc.file_name or ""
        if doc.file_size and doc.file_size > _DOMAIN_FILE_MAX:
            return [], f"Файл слишком большой ({doc.file_size // 1024} КБ), максимум {_DOMAIN_FILE_MAX // 1024} КБ."
        if fname and not re.search(r'\.(txt|list|lst|csv|conf)$', fname, re.I) and (doc.mime_type or "").split("/")[0] != "text":
            return [], f"Ожидаю текстовый файл со списком доменов, получил: {fname}"
        try:
            data = bot.download_file(bot.get_file(doc.file_id).file_path)
            text += "\n" + data.decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning("domain file download failed: %s", e)
            return [], "Не удалось скачать файл, попробуйте ещё раз."
    return _parse_domains(text), None


def _handle_slot_add(message: types.Message, slot: int):
    retry = handle_slot1_add if slot == 1 else handle_slot2_add
    try:
        if not getattr(message, 'document', None) and _is_cancel(message.text):
            bot.send_message(message.chat.id, 'Отменено.', reply_markup=_hosts_keyboard())
            return
        if not getattr(message, 'document', None) and _looks_like_menu_nav(message.text):
            bot.send_message(message.chat.id, 'Ок, добавление отменено.', reply_markup=_hosts_keyboard())
            return
        domains, err = _domains_from_message(message)
        if err:
            answer = bot.send_message(message.chat.id, err, reply_markup=_cancel_kb())
            bot.register_next_step_handler(answer, retry)
            return
        if not domains:
            answer = bot.send_message(
                message.chat.id,
                "Не вижу ни одного домена. Пришлите домены текстом (через пробел, запятую или по строке) "
                "или .txt-файлом со списком.",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, retry)
            return
        n = len(domains)
        if n == 1:
            announce = f"Добавляю {domains[0]} в slot-{slot}…"
        else:
            announce = f"Добавляю {n} доменов в slot-{slot}… (dnsmasq обновится один раз в конце)"
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "vless", f"{slot}-add"] + domains,
            announce_text=announce,
            settle_seconds=3,
        )
    except Exception as e:
        logger.exception("Error in _handle_slot_add(%d): %s", slot, str(e))
        try:
            bot.send_message(message.chat.id, "Ошибка при добавлении домена.")
        except Exception:
            pass


def handle_slot1_add(message: types.Message):
    _handle_slot_add(message, 1)


@bot.message_handler(regexp=r"^Slot\-1: \-домен$", chat_types=["private"])
def vless_slot1_del_prompt(message: types.Message):
    try:
        logger.info("User %s prompted slot-1 -domain", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Введите домен(ы) для удаления из slot-1. Будут убраны из VPN-списка полностью (пойдут напрямую через ISP).",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_slot1_del)
    except Exception as e:
        logger.exception("Error in vless_slot1_del_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def handle_slot1_del(message: types.Message):
    try:
        if _is_cancel(message.text if not getattr(message, 'document', None) else ''):
            bot.send_message(message.chat.id, 'Отменено.', reply_markup=_hosts_keyboard())
            return
        domains = _parse_domains(message.text)
        if not domains:
            answer = bot.send_message(
                message.chat.id,
                "Не вижу ни одного домена. Пришлите список ещё раз (через пробел или новые строки).",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, handle_slot1_del)
            return
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "vless", "1-del"] + domains,
            announce_text=f"Удаляю {len(domains)} домен(ов) из slot-1…",
            settle_seconds=3,
        )
    except Exception as e:
        logger.exception("Error in handle_slot1_del: %s", str(e))
        try:
            bot.send_message(message.chat.id, "Ошибка при удалении домена.")
        except Exception:
            pass


@bot.message_handler(regexp=r"^Slot\-2: \+домен$", chat_types=["private"])
def vless_slot2_add_prompt(message: types.Message):
    try:
        logger.info("User %s prompted slot-2 +domain", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Пришлите домен(ы) для slot-2.\n"
            "Можно сразу много: через пробел, запятую, по одному на строку — или .txt-файлом со списком.",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_slot2_add)
    except Exception as e:
        logger.exception("Error in vless_slot2_add_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def handle_slot2_add(message: types.Message):
    _handle_slot_add(message, 2)


@bot.message_handler(regexp=r"^Slot\-2: \-домен$", chat_types=["private"])
def vless_slot2_del_prompt(message: types.Message):
    try:
        logger.info("User %s prompted slot-2 -domain", message.from_user.username)
        answer = bot.send_message(
            message.chat.id,
            "Введите домен(ы) для удаления из slot-2. Будут убраны из VPN-списка полностью (пойдут напрямую через ISP). Если хотите просто перенести в slot-1 — используйте «Slot-1: +домен».",
            reply_markup=_cancel_kb(),
        )
        bot.register_next_step_handler(answer, handle_slot2_del)
    except Exception as e:
        logger.exception("Error in vless_slot2_del_prompt: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


def handle_slot2_del(message: types.Message):
    try:
        if _is_cancel(message.text if not getattr(message, 'document', None) else ''):
            bot.send_message(message.chat.id, 'Отменено.', reply_markup=_hosts_keyboard())
            return
        domains = _parse_domains(message.text)
        if not domains:
            answer = bot.send_message(
                message.chat.id,
                "Не вижу ни одного домена. Пришлите список ещё раз (через пробел или новые строки).",
                reply_markup=_cancel_kb(),
            )
            bot.register_next_step_handler(answer, handle_slot2_del)
            return
        _run_pivas_and_reply(
            message.chat.id,
            ["pivas", "vless", "2-del"] + domains,
            announce_text=f"Снимаю {len(domains)} домен(ов) со slot-2…",
        )
    except Exception as e:
        logger.exception("Error in handle_slot2_del: %s", str(e))
        try:
            bot.send_message(message.chat.id, "Ошибка при снятии домена.")
        except Exception:
            pass


@bot.message_handler(regexp=r"^Slot\-1: список$", chat_types=["private"])
def vless_slot1_list(message: types.Message):
    try:
        logger.info("User %s requested slot-1 list", message.from_user.username)
        with tempfile.TemporaryFile() as tempf:
            proc = subprocess.Popen(
                ["pivas", "vless", "1-list"], stdout=tempf, stderr=subprocess.STDOUT
            )
            proc.wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
        bot.send_message(
            message.chat.id,
            mcode("\n" + output + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in vless_slot1_list: %s", str(e))
        bot.send_message(message.chat.id, "Ошибка при получении списка slot-1.")


@bot.message_handler(regexp=r"^Slot\-2: список$", chat_types=["private"])
def vless_slot2_list(message: types.Message):
    try:
        logger.info("User %s requested slot-2 list", message.from_user.username)
        with tempfile.TemporaryFile() as tempf:
            proc = subprocess.Popen(
                ["pivas", "vless", "2-list"], stdout=tempf, stderr=subprocess.STDOUT
            )
            proc.wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
        bot.send_message(
            message.chat.id,
            mcode("\n" + output + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in vless_slot2_list: %s", str(e))
        bot.send_message(message.chat.id, "Ошибка при получении списка slot-2.")


@bot.message_handler(regexp=r"^(?:Откат VLESS|Откат подключений)$", chat_types=["private"])
def vless_rollback(message: types.Message):
    try:
        logger.info("User %s requested vless rollback", message.from_user.username)
        bot.send_message(
            message.chat.id,
            "Откат конфига, xray перезапускается…",
        )
        with tempfile.TemporaryFile() as tempf:
            proc = subprocess.Popen(["pivas", "vless", "rollback"], stdout=tempf, stderr=subprocess.STDOUT)
            proc.wait()
            tempf.seek(0)
            output = clean_string(tempf.read().decode("utf-8"))
        time.sleep(3)
        _send_with_retry(
            message.chat.id,
            mcode("\n" + output + "\n"),
            parse_mode="MarkdownV2",
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in vless_rollback: %s", str(e))
        try:
            bot.send_message(message.chat.id, "Произошла ошибка при откате VLESS.")
        except Exception:
            pass


@bot.message_handler(regexp="Обновить бота", chat_types=["private"])
def update_bot(message: types.Message):
    """
    В официальный бот-репозиторий кнопка качала свежий релиз с GitHub и
    ставила его поверх. У нас бот пропатчен (slot-1/slot-2 кнопки, proxy через
    xray-1098, и т.п.), апгрейд с upstream ЗАТИРАЕТ все наши правки → даунгрейд.
    Поэтому эта кнопка не скачивает upstream-релиз. Для своего IPK есть
    отдельная кнопка «Обновить пакет».
    """
    try:
        logger.info("User %s нажал 'Обновить бота' — игнорирую upstream upgrade",
                    message.from_user.username)
        bot.send_message(
            message.chat.id,
            (
                f"Текущая версия: {BOT_VERSION}\n\n"
                "Чтобы установить новую сборку Pivas, нажмите «Сервис → "
                "Обновить пакет» и отправьте .ipk как документ."
            ),
            reply_markup=_service_keyboard(),
        )
    except Exception as e:
        logger.exception("Error in update_bot: %s", str(e))
        try:
            bot.send_message(message.chat.id, "Произошла ошибка.")
        except Exception:
            pass


@bot.message_handler(regexp="Назад", chat_types=["private"])
def go_back(message: types.Message):
    try:
        logger.info("User %s requested to go back", message.from_user.username)
        handle_start(message)
    except Exception as e:
        logger.exception("Error in go_back: %s", str(e))
        bot.send_message(message.chat.id, "Произошла ошибка, попробуйте позже.")


@bot.message_handler(regexp="^Отмена$", chat_types=["private"])
def global_cancel(message: types.Message):
    """
    Страховочный handler: если юзер жмёт "Отмена" когда next_step_handler
    уже не висит (например reply-клава зависла после _web_send_status),
    отменяем возможные висящие шаги и возвращаем в сервисное меню.
    telebot вызывает next_step_handler раньше message_handler, так что в
    нормальных сценариях (ввод пароля/домена) этот handler не перехватит.
    """
    try:
        try:
            bot.clear_step_handler_by_chat_id(message.chat.id)
        except Exception:
            pass
        bot.send_message(message.chat.id, "Отменено.", reply_markup=_service_keyboard())
    except Exception as e:
        logger.exception("global_cancel: %s", e)


def _proxy_alive():
    """TCP-connect на proxy-порт (xray :1098). None — прокси не настроен."""
    if not _proxy_url:
        return None
    import socket, urllib.parse
    p = urllib.parse.urlparse(_proxy_url)
    host = p.hostname or "127.0.0.1"
    port = p.port or (443 if p.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


def _wait_for_proxy(timeout=None):
    """
    Ждём, пока xray поднимет proxy-порт. После ребута — секунды; после
    `pivas stop all` — пока пользователь не сделает `pivas start`. Ждём тихо:
    без прокси api.telegram.org недоступен и любые попытки только засоряют лог.
    timeout=None — ждать бесконечно, писать в лог раз в минуту.
    """
    if not _proxy_url:
        return True
    start = time.monotonic()
    last_log = 0.0
    while True:
        if _proxy_alive():
            logger.info("Proxy %s готов", _proxy_url)
            return True
        now = time.monotonic()
        if timeout is not None and now - start > timeout:
            logger.warning("Proxy %s не отвечает за %sс", _proxy_url, timeout)
            return False
        if now - last_log > 60:
            logger.warning("Proxy %s не отвечает (xray остановлен?) — жду", _proxy_url)
            last_log = now
        time.sleep(3)


def _run_polling_forever():
    """
    Свой цикл вместо bot.infinity_polling: telebot при мёртвом прокси печатает
    полный traceback в stderr каждые 3 секунды (stdout-лог рос до гигабайта).
    Здесь при недоступном прокси молча ждём его возврата.
    """
    import telebot as _tb
    _tb.logger.setLevel(logging.CRITICAL)
    while True:
        if _proxy_alive() is False:
            _wait_for_proxy(timeout=None)
            continue
        try:
            bot.polling(non_stop=True, skip_pending=True, timeout=60,
                        long_polling_timeout=20, logger_level=None)
        except KeyboardInterrupt:
            raise
        except Exception as e:
            if _proxy_alive() is False:
                logger.warning("polling: прокси пропал (%s) — жду xray", type(e).__name__)
            else:
                logger.error("polling: %s: %s — повтор через 10с", type(e).__name__, str(e)[:200])
                time.sleep(10)


if __name__ == "__main__":
    try:
        _wait_for_proxy(timeout=None)
        bot.setup_middleware(Middleware())
        connection_attempt = 0
        while connection_attempt != telegram_bot_config.reconnection_attempts:
            connection_attempt +=1
            logger.info(f'Trying to connect to the telegram server. Attempt №{connection_attempt}')
            os.system(
                f"logger -s -t telegram4pivas Trying to connect to the telegram server. Attempt №{connection_attempt}"
                      )
            try:
                bot_me = bot.get_me()
                connection_attempt = telegram_bot_config.reconnection_attempts
            except Exception as e:
                if _proxy_alive() is False:
                    # xray лёг между проверкой и запросом — попытку не считаем
                    connection_attempt -= 1
                    _wait_for_proxy(timeout=None)
                    continue
                if connection_attempt != telegram_bot_config.reconnection_attempts:
                    logger.warning(f'Connection attempt №{connection_attempt} failed. Wait {telegram_bot_config.reconnection_timeout} seconds before trying to connect again.')
                    os.system(
                        f"logger -s -t telegram4pivas Connection attempt №{connection_attempt} failed. Wait {telegram_bot_config.reconnection_timeout} seconds before trying to connect again."
                             )
                    time.sleep(telegram_bot_config.reconnection_timeout)
                else:
                    logger.warning(f'Connection failed after {connection_attempt} attempts.')
                    logger.error(f'Connection failed after {connection_attempt} attempts. Check internet connection! Bot is shutdown.')
                    os.system(
                        f"logger -s -t telegram4pivas Connection failed after {connection_attempt} attempts. Check internet connection! Bot shutdown."
                             )
                    sys.exit()
        os.system(
            f"logger -s -t telegram4pivas Connection successful. Bot @{bot_me.username} running [{BOT_VERSION}]."
                 )
        logger.info(f'Connection successful. Bot @{bot_me.username} running [{BOT_VERSION}].')
        send_startup_message()
        _run_polling_forever()
    except Exception as e:
        logger.exception("Fatal error occurred while running the bot")
