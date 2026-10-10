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

| Архитектура Entware | Прямое скачивание |
| --- | --- |
| `mipsel-3.4` | [Скачать install-pivas-full.sh — MIPSel](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-mipsel.sh) |
| `aarch64-3.10` (в том числе KN-1811) | [Скачать install-pivas-full.sh — AArch64](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/install-pivas-full-aarch64.sh) |

Это готовые установщики со встроенным полным пакетом. Выберите архитектуру по `opkg print-architecture`, скачайте файл и при передаче на роутер назовите его **`/opt/tmp/install-pivas-full.sh`**. Для новой установки достаточно одного подходящего `.sh` и доступа к репозиторию Entware.

[Все файлы 1.2.0-beta.1](https://github.com/Georgy-Benelli/pivas/releases/tag/v1.2.0-beta.1) · [SHA256SUMS](https://github.com/Georgy-Benelli/pivas/releases/download/v1.2.0-beta.1/SHA256SUMS)

## Файлы релиза

- `install-pivas-full-mipsel.sh` / `install-pivas-full-aarch64.sh` — полный установщик для новой установки.
- `pivas-full_...ipk` — полный пакет для обновления через SSH или Telegram.
- `SHA256SUMS` и `manifest.json` — контрольные суммы и версии компонентов.
- `LICENSE` и `THIRD_PARTY_NOTICES.md` — лицензии и происхождение компонентов.

Архитектуру определяйте по `opkg print-architecture`. Требуются Entware, EXT, Netfilter и Proxy client. После установки настройте ссылки и явно запустите Pivas; веб и бот включаются отдельно.

## Ограничения

IPv4-маршрутизация; IPv6 и собственный DoH приложения не охватываются автоматически. В 1.2.0-beta.1 импорт XHTTP не принимает дублированный `mode` внутри JSON `extra`. Hysteria 2 требует доступного UDP. MIPSel использует сборку Go 1.26.6 для совместимости; AArch64 — официальный бинарник Xray.

Это предварительный выпуск. Результаты локальной проверки подготовки репозитория — в `VALIDATION.md`; CI и проверка на конкретных устройствах различаются. До публикации полного комплекта нужно закрыть вопрос лицензии исходного Telegram-бота, описанный в `THIRD_PARTY_NOTICES.md`.
