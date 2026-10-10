# Публикация релиза

Соберите пакет по [инструкции](DEVELOPMENT.md), проверьте `tests/verify_dist.py`, `tools/check_public.py` и результаты CI. Версии задаются в `build.py`.

Создайте тег на коммите выпуска. Используйте [русские](RELEASE_NOTES.md) и [английские](RELEASE_NOTES.en.md) примечания для описания; относительные ссылки замените ссылками на документы в GitHub.

Прикрепите восемь файлов из `release-assets/`:

| Файл | Назначение |
| --- | --- |
| `install-pivas-full-mipsel.sh`, `install-pivas-full-aarch64.sh` | Установка |
| Два `pivas-full_*.ipk` | Обновление |
| `manifest.json`, `SHA256SUMS` | Версии и контрольные суммы |
| `LICENSE`, `THIRD_PARTY_NOTICES.md` | Лицензии компонентов |

Workflow сборки готовит файлы для скачивания из Actions. Сам релиз он не публикует.

Перед распространением полного пакета нужно уточнить разрешение автора исходного Telegram-бота. Подробности — в [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).
