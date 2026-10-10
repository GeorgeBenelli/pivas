# Pivas 1.2.0-beta.1 — восстановление DNS-служб

**Русский** · [English](RELEASE_NOTES.en.md)

Установка и запуск теперь обнаруживают отсутствующие init-скрипты dnsmasq и dnscrypt-proxy2 и восстанавливают их из локальных шаблонов. Это устраняет ситуацию, когда пакеты числятся установленными, но `pivas start` завершается `S56dnsmasq: not found` или отсутствием `S09dnscrypt-proxy2`.

Добавлена команда `pivas repair-dns`; состояние файлов служб включено в диагностику, а веб показывает причину отказа запуска. Существующие настройки DNS сохраняются. Отсутствующие бинарники и повреждённые зависимости требуют исправления по сообщению проверки.

## Состав

- Pivas `1.1.9_beta-10-25-custom25`.
- Веб `1.0-custom20`, Telegram `1.2-custom14`.
- Xray `26.3.27-2`, обёртка `26.3.27-1-custom4`.
- QUIC-помощник `0.1-1`.

### Скачать полный установщик

| Архитектура Entware | Прямое скачивание | Команда запуска в SSH |
| --- | --- | --- |
| `mipsel-3.4` | [Скачать install-pivas-full-mipsel.sh — MIPSel](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) | `sh /opt/tmp/install-pivas-full-mipsel.sh` |
| `aarch64-3.10` (в том числе KN-1811) | [Скачать install-pivas-full-aarch64.sh — AArch64](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) | `sh /opt/tmp/install-pivas-full-aarch64.sh` |

Это готовые установщики со встроенным полным пакетом. Выберите архитектуру по `opkg print-architecture`, скачайте подходящий файл и передайте его в **`/opt/tmp/`** роутера, сохранив имя. Переименовывать файл не нужно. Для новой установки достаточно одного подходящего `.sh` и доступа к репозиторию Entware.

[Все файлы 1.2.0-beta.1](https://github.com/Georgy-Benelli/pivas/releases/tag/v1.2.0-beta.1) · [SHA256SUMS](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/SHA256SUMS)

## Файлы релиза

- `install-pivas-full-mipsel.sh` / `install-pivas-full-aarch64.sh` — полный установщик для новой установки.
- `pivas-full_...ipk` — полный пакет для обновления через SSH или Telegram.
- `SHA256SUMS` и `manifest.json` — контрольные суммы и версии компонентов.
- `LICENSE` и `THIRD_PARTY_NOTICES.md` — лицензии и происхождение компонентов.

Архитектуру определяйте по `opkg print-architecture`. Требуются Entware, EXT, Netfilter и Proxy client. После установки настройте ссылки и явно запустите Pivas; веб и бот включаются отдельно.

## Ограничения

IPv4-маршрутизация; IPv6 и собственный DoH приложения не охватываются автоматически. В 1.2.0-beta.1 импорт XHTTP не принимает дублированный `mode` внутри JSON `extra`. Hysteria 2 требует доступного UDP. MIPSel использует сборку Go 1.26.6 для совместимости; AArch64 — официальный бинарник Xray.

Это предварительный выпуск. Актуальные [инструкция установки](INSTALL.md) и [результаты проверки](VALIDATION.md) находятся в ветке `main`; архив исходников по тегу отражает состояние на момент выпуска. CI и проверка на конкретных устройствах различаются. Вопрос разрешения на перераспространение исходного Telegram-бота остаётся открытым: [происхождение и лицензии](../THIRD_PARTY_NOTICES.md).
