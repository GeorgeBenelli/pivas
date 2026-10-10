# Pivas

Русский · [English](README.en.md)

Pivas направляет выбранные сайты и IP-сети через Xray на роутере Keenetic. Остальной трафик идёт через обычное подключение. Списками и подключениями можно управлять в вебе или Telegram.

**1.2.0-beta.1** · Xray 26.3.27 · MIPSel / AArch64

![Pivas](docs/images/dashboard-dark-demo.png)

[Светлая тема](docs/images/dashboard-light-demo.png) · [На телефоне](docs/images/dashboard-mobile-demo.png)

## Возможности

- Два подключения с отдельными списками сайтов. Ссылки можно поменять местами одной кнопкой.
- VLESS: TCP + Reality, gRPC + TLS, XHTTP + TLS/Reality. Hysteria 2, в том числе с Salamander.
- Группы доменов, отдельные домены, IPv4 и CIDR. Поддомены учитываются автоматически.
- DNS для сайтов из списков — через их слот; для остальных — штатный DNS Keenetic.
- До 10 устройств без Pivas. Исключения привязаны к MAC-адресу.
- Управление группами и слотами, диагностика и обновление из Telegram.
- Светлая и тёмная тема, резервные копии настроек, просмотр нагрузки.

## Установка

Нужны Entware и компоненты KeeneticOS: Proxy client, поддержка открытых пакетов, Netfilter и EXT. Для USB-накопителя используйте EXT4. [Подробная инструкция](docs/INSTALL.md).

Узнайте архитектуру в SSH Entware:

```sh
opkg print-architecture
```

Скачайте подходящий установщик и передайте его в `/opt/tmp/`, сохранив имя. Выполните `opkg update`, затем **одну** команду из таблицы:

| Архитектура Entware | Установщик | Команда |
| --- | --- | --- |
| `mipsel-3.4` | [install-pivas-full-mipsel.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` | [install-pivas-full-aarch64.sh](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

Установщик попросит ссылки подключений, токен бота и ID администратора. Поля можно пропустить Enter и заполнить позже. Веб, бот и маршрутизация включаются отдельно.

Чтобы продолжить настройку в вебе:

```sh
pivas web on 'ВАШ_ПАРОЛЬ'
```

Откройте `http://АДРЕС_РОУТЕРА:8888`, логин `admin`. Добавьте ссылку, создайте группу и включите Pivas. Из SSH: `pivas start`.

Для обновления скачайте полный `.ipk` своей архитектуры из [релиза](https://github.com/Georgy-Benelli/pivas/releases/tag/v1.2.0-beta.1). В боте: «Сервис → Обновить пакет». [Обновление через SSH и Telegram](docs/UPDATING.md).

## Документация

[Настройка](docs/SETUP.md) · [DNS и маршруты](docs/ARCHITECTURE.md) · [Решение проблем](docs/TROUBLESHOOTING.md) · [Сборка](docs/DEVELOPMENT.md) · [История изменений](docs/CHANGELOG.md)

Маршрутизация работает с IPv4. IPv6 и собственный DoH браузера могут обходить правила. Веб предназначен для локальной сети — порт 8888 не следует открывать в интернет. [Безопасность](SECURITY.md).

## Исходные проекты

Pivas основан на [KVAS](https://github.com/qzeleza/kvas) и [telegram4kvas](https://github.com/dnstkrv/telegram4kvas), использует Xray и pyTelegramBotAPI. Лицензии компонентов и вопрос разрешения на распространение исходного бота описаны в [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) и [LICENSE](LICENSE).
