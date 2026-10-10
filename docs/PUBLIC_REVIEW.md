# Проверка публичного комплекта — 2026-10-10

Повторная проверка выполнена для опубликованной ветки `main` на коммите `bcae24d` и релиза `v1.2.0-beta.1`. После проверки исправлены отчёты и ссылки документации. Роутеры не использовались.

## Что проверено

- 221 опубликованный файл, включая 218 текстовых файлов и три демонстрационных изображения. Дерево GitHub совпало с локальными исходниками.
- Все шесть коммитов публичной истории и доступные через ветку и тег версии файлов; адреса авторов и коммитеров — GitHub noreply.
- Восемь файлов релиза, заново скачанных с GitHub. Проверены SHA-256 из API, `manifest.json` и `SHA256SUMS`, вложенные IPK, заголовки установщиков и четыре ELF-бинарника.
- Последний успешный журнал Source checks, метаданные репозитория, релиза, веток и тегов. Открытых и закрытых задач нет; сохранённых артефактов Actions нет.
- Три текущих и три исторических скриншота: проверены метаданные PNG; визуально просмотрены текущий тёмный экран и исторические тёмный и мобильный экраны. Это демонстрационные данные, без личных настроек роутера.
- Относительные Markdown-ссылки и десять внешних ссылок пользовательской документации. Названия скачиваемых установщиков совпадают с командами запуска.

Сканер проверил 336 уникальных объектов, включая восемь вложенных архивов и журналы CI. Совпадений с известными личными данными и шаблонами реальных секретов не найдено. 95 кандидатов, похожих на адреса, UUID и ссылки подключения, дополнительно разобраны: это тестовые заглушки, документация сторонних компонентов, GitHub noreply и временные идентификаторы CI. Значения кандидатов и перечень личных строк не публикуются.

Не обнаружены реальные токены Telegram/GitHub/облачных сервисов, приватные ключи, известные личные пароли и ссылки подключения, персональные домашние пути, конфигурации роутеров или резервные копии. В изображениях нет EXIF и текстовых метаданных. Имя публичного аккаунта GitHub остаётся видимым.

## Исправления и ограничения

Обновлены устаревшие сведения о числе файлов сборки и статусе публикации. В примечаниях к релизу добавлены рабочие ссылки на актуальную установку, проверку и лицензии. Установщики и IPK не менялись.

История Git сохраняет прежние названия выпуска, формулировки и демонстрационные иллюстрации. Удаление файла из текущей ветки не удаляет его из старых коммитов. Тег релиза фиксирует исходники на момент выпуска; актуальная инструкция установки находится в ветке `main` и в описании релиза.

Поиск шаблонов и ручной просмотр не гарантируют обнаружение любого возможного секрета и не заменяют полный аудит безопасности. Проверка не является юридическим заключением. Вопрос разрешения на перераспространение исходного Telegram-бота остаётся открытым: [происхождение и лицензии](../THIRD_PARTY_NOTICES.md).

## English

The repeat audit covered published `main` at `bcae24d`, all six public commits and release `v1.2.0-beta.1`: 221 source files, eight freshly downloaded release assets, nested packages, four ELF binaries, commit metadata and the latest successful CI logs. Published source matched the local checkout; release digests, manifests and embedded payloads were verified. Documentation links and installer filenames were checked. There were no issues or retained Actions artifacts.

The scan covered 336 unique objects and eight nested archives. No known personal data or real secret patterns were found. All 95 credential-shaped candidates were classified as fixtures, upstream documentation, GitHub noreply addresses or temporary CI identifiers. PNG metadata was checked for all six current and historical screenshots; selected screenshots were also visually reviewed. No personal router settings were found. Private scan values are not published.

Stale validation text and release documentation links were corrected. Runtime packages were unchanged. Earlier wording and demonstration images remain in Git history. The release tag is a source snapshot; use `main` and the release description for current installation instructions. Pattern scanning is not a guarantee of absence of secrets or a full security audit. The upstream Telegram bot redistribution permission remains unresolved; see [third-party notices](../THIRD_PARTY_NOTICES.md).
