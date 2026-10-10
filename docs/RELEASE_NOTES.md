# Pivas 1.2.0-beta.1

[English](RELEASE_NOTES.en.md)

Исправлен запуск на установках, где отсутствовали `S56dnsmasq` или `S09dnscrypt-proxy2`. Pivas восстанавливает эти скрипты из шаблонов, сохраняя DNS-настройки. Если повреждены сами зависимости, сообщает, какой пакет нужно восстановить.

- Добавлена команда `pivas repair-dns`.
- В диагностике видно состояние DNS-служб.
- Веб показывает причину ошибки запуска.

## Установка

Скачайте установщик своей архитектуры, передайте в `/opt/tmp/` без переименования. В SSH выполните `opkg update`, затем команду из таблицы.

| Архитектура Entware | Установщик | Команда |
| --- | --- | --- |
| `mipsel-3.4` | [install-pivas-full-mipsel.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` | [install-pivas-full-aarch64.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

Нужны Entware и компоненты KeeneticOS из [инструкции](INSTALL.md). После установки настройте подключение и запустите Pivas. Веб и бот включаются отдельно.

## Обновление

Для обновления скачайте `pivas-full_1.2.0-beta.1_*.ipk` своей архитектуры. Он обновляет Pivas, веб, бот и Xray вместе. [Через Telegram или SSH](UPDATING.md).

Внутри — Xray 26.3.27. Версии остальных компонентов и контрольные суммы записаны в `manifest.json`; `SHA256SUMS` позволяет проверить скачанные файлы.

Это бета-версия. Маршруты работают с IPv4; Hysteria 2 требует UDP. В XHTTP пока нужно убрать дублирующее поле `mode` из `extra`, если оно уже есть в URL. [Подробнее](TROUBLESHOOTING.md).

[Лицензии компонентов](../THIRD_PARTY_NOTICES.md). Разрешение на распространение исходного Telegram-бота пока не уточнено.
