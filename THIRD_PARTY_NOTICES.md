# Лицензии и авторы

Компоненты Pivas распространяются под разными лицензиями. Сведения об исходных проектах проверены 9 октября 2026 года.

| Компонент | Источник | Условия / статус |
| --- | --- | --- |
| Исходная обвязка KVAS | [qzeleza/kvas](https://github.com/qzeleza/kvas) | Apache-2.0, исходный [LICENCE.md](licenses/KVAS-LICENCE.md) сохранён |
| Исходный Telegram-бот | [dnstkrv/telegram4kvas](https://github.com/dnstkrv/telegram4kvas) | В просмотренном дереве main не найден файл лицензии; разрешение на перераспространение требует уточнения |
| Встроенная библиотека telebot | [pyTelegramBotAPI](https://github.com/eternnoir/pyTelegramBotAPI) | [GPL-2.0](licenses/pyTelegramBotAPI-GPL-2.0.txt); уведомления отдельных файлов сохранены |
| Xray-core 26.3.27 | [XTLS/Xray-core](https://github.com/XTLS/Xray-core/tree/v26.3.27) | [MPL-2.0](vendor/xray/LICENSE), ссылки на точные исходники и хеши в [манифесте](vendor/xray/manifest.json) |
| Go runtime | [Go](https://go.dev/) | [BSD-style](vendor/xray/GO-LICENSE) |
| QUIC-помощник и зависимости | [go.mod](tools/quic-probe/go.mod) | Условия зависимостей собраны в [LICENSES.txt](vendor/quic-probe/LICENSES.txt) |


Xray для AArch64 взят из официального релиза. MIPSel собран из неизменённых исходников v26.3.27 с Go 1.26.6. Параметры сборки и хеши записаны в [манифесте](vendor/xray/manifest.json). Изменения Pivas перечислены в [истории версий](docs/CHANGELOG.md).

## Исходный Telegram-бот

В репозитории telegram4kvas не найден файл лицензии. Разрешение автора на распространение этого кода нужно уточнить либо заменить его. Лицензия Apache-2.0 проекта Pivas на этот код не распространяется.

Пакеты зависимостей Entware скачиваются отдельно. Их лицензии определяются соответствующими проектами.
