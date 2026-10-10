# Установка

## Подготовка роутера

В компонентах KeeneticOS должны быть установлены:

- Поддержка открытых пакетов.
- Поддержка файловой системы EXT.
- Модули ядра подсистемы Netfilter.
- Proxy client — найдите его по слову `proxy` в общем списке компонентов.

Установите [Entware](https://support.keenetic.com/hero/kn-1012/en/20980-installing-the-entware-repository-on-a-usb-drive.html). Для USB-флешки используйте EXT4. Подключитесь к Entware по SSH с правами root; обычно это порт 222.

При замене флешки выберите новый накопитель в настройках OPKG. На пустую флешку Entware нужно установить заново.

## Выбор установщика

В SSH выполните:

```sh
opkg print-architecture
```

Скачайте файл для полученной архитектуры и передайте его через SFTP/SCP в `/opt/tmp/`. Имя файла оставьте как есть.

| Архитектура Entware | Установщик | Команда |
| --- | --- | --- |
| `mipsel-3.4` | [install-pivas-full-mipsel.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` | [install-pivas-full-aarch64.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

`mips` и `mipsel` — разные архитектуры. Выбирайте по выводу Entware; строки `arch: mips` в KeeneticOS недостаточно. Сборки для MIPS big-endian нет.

## Запуск

Выполните `opkg update`, затем команду из таблицы для своей архитектуры.

Установщик содержит Pivas, Xray, веб и бот. Зависимости он скачивает из Entware. Во время установки можно задать ссылки двух слотов, токен бота и Telegram ID администратора. Нажмите Enter, если хотите заполнить их позже.

После установки Pivas остаётся на паузе. Включение веба, настройка слотов и запуск описаны в [SETUP.md](SETUP.md).

Если установщик сообщает, что сетевые файлы принадлежат другому пакету, сначала разберите конфликт. `--force-overwrite` может повредить его настройки.

## IPv6

Правила Pivas работают с IPv4. Если хотите исключить прямой выход по IPv6, отключите IPv6 у интернет-подключения Keenetic. Проверить маршрут можно командой `ip -6 route show default`. Имена WAN-интерфейсов зависят от настроек роутера.

## Проверка скачанного файла

В релизе есть `SHA256SUMS`. Проверка необязательна для запуска установщика. Чтобы проверить весь набор файлов, скачайте его и выполните в той же папке `shasum -a 256 -c SHA256SUMS` на macOS или `sha256sum -c SHA256SUMS` на Linux.
