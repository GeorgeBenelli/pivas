# Разработка

## Тесты

Нужны Python 3.11+, `sh`, `tar` и `openssl`. Тесты работают во временных каталогах, без роутера и прав root.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python tools/check_public.py
python -m unittest discover -s tests -q
```

## Веб на компьютере

```sh
python tests/preview_web.py 8879
```

Откройте `http://127.0.0.1:8879`. Демо использует тестовые настройки и отключённую авторизацию; сервер слушает только loopback.

В другом терминале запустите браузерные тесты. Нужен Node.js 22:

```sh
npm ci
npx playwright install chromium
npm run test:web
```

Для своих установок Playwright и Chromium задайте `PLAYWRIGHT_MODULE` и `CHROMIUM_PATH`. Скриншоты тестов сохраняются в `docs/ui-*.png` и исключены из Git.

## Сборка пакетов

Нужен Go 1.26.6. Путь к компилятору можно задать через `GO_BINARY`.

```sh
python tools/fetch_xray.py
python tools/build_quic_probe.py
python build.py
python tests/verify_dist.py
python tools/package_release.py
```

Для AArch64 загружается официальный Xray 26.3.27. Версия MIPSel собирается из тех же исходников с Go 1.26.6 и softfloat для совместимости с ядром Keenetic. QUIC-помощник собирается для обеих архитектур. Адреса исходников, версии и хеши находятся в манифестах `vendor/` и в `go.mod`/`go.sum`.

`build.py` запускает тесты и пересоздаёт `dist/`. Не храните там свои файлы. В каталоге будут IPK компонентов, два полных пакета и по одному установщику `install-pivas-full.sh` для каждой архитектуры.

`verify_dist.py` проверяет хеши и содержимое пакетов. `package_release.py` собирает восемь файлов для публикации в `release-assets/`: два установщика с суффиксом архитектуры, два полных IPK, лицензии, манифест и контрольные суммы. [Публикация релиза](RELEASE.md).

Кэши сборки находятся во временной директории ОС. При необходимости задайте `GOCACHE`, `GOMODCACHE` и `GOPATH`. Бинарники vendor не хранятся в Git.

## Каталоги

| Каталог | Что внутри |
| --- | --- |
| `work/orig/data` | Скрипты Pivas и файлы для `/opt` |
| `work/orig/control` | Метаданные и скрипты установки |
| `web-src` | Веб-сервер и интерфейс |
| `bot` | Telegram-бот и библиотека telebot |
| `xray-src` | Скрипт запуска Xray |
| `tools/quic-probe` | Помощник для проверки QUIC-сертификатов |
| `vendor` | Манифесты, лицензии и загружаемые бинарники |
| `tests` | Модульные, браузерные и сетевые тесты |

GitHub Actions запускает модульные и браузерные тесты. Сборка пакетов запускается вручную отдельным workflow. Тестам транспортов `*_e2e.py` нужны локальные серверы и бинарники из их настроек.

На роутере отдельно проверяются холодный запуск, смена WAN, DNS, маршруты и исключения устройств.
