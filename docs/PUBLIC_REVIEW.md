# Проверка публичного комплекта — 2026-10-09

Проверены рабочее дерево публичного репозитория, все доступные локально Git-объекты его первоначальной истории, метаданные автора/коммитера, документы и вложенное содержимое релизных архивов, IPK и самораспаковывающихся установщиков. Бинарники также проверены на известные личные строки и домашние пути.

Не обнаружены реальные токены Telegram/GitHub, приватные ключи, известные личные пароли и ссылки подключения, адреса личных серверов и удалённых устройств, домашние пути пользователя или сохранённые пользовательские конфигурации. Найденные примеры ссылок и учётных данных проверены: это заглушки и тестовые данные. Адрес автора коммита — GitHub noreply; имя аккаунта владельца остаётся публичным.

Все три скриншота пересняты в локальном демо. На них вымышленные группы и домены `.example`, без реальных настроек роутера. Имена файлов изменены, чтобы страницы не показывали старые изображения из кэша.

Документация и описание проекта обновлены. Новые отдельные `.sh` совпадают побайтно с полными установщиками из предыдущего выпуска; содержимое пакетов и рабочая логика не менялись. Проверены SHA-256, заголовки shell и встроенные IPK. Технические имена в исходном коде и исходные уведомления сторонних компонентов сохранены.

Поиск известных строк и шаблонов не доказывает отсутствие любых возможных секретов. Проверка не распространяется на приватные рабочие каталоги вне публичной копии и не является юридическим заключением. Старые коммиты сохраняют прежние иллюстрации и формулировки; история не переписывалась.

## English

The public source tree, initial Git history and author metadata, release archives, nested IPKs and embedded installers were inspected. Checks included known private values, common credential patterns and personal home paths, including in binaries. No matching personal credentials or device/server references were found. Connection examples were reviewed as placeholders and test data. The author's address uses GitHub noreply; the repository owner's account remains public.

All three documentation screenshots were regenerated from fictional local fixtures. Standalone installers are byte-identical to the previously published embedded installers. Package contents and runtime behaviour are unchanged. Pattern-based checks cannot guarantee that every possible secret has been detected. Existing Git history and third-party notices remain intact.

## Переименование выпуска — 2026-10-10

Публичная версия и версия полного IPK теперь `1.2.0-beta.1`. Полные установщики и IPK пересобраны с новой метаинформацией; их контрольные суммы изменились. Файлы работающих компонентов сохранены. Отдельные архивы комплекта больше не распространяются.

The public release and full IPK now use `1.2.0-beta.1`. Installers and full packages were rebuilt with updated metadata and checksums. Component payloads are unchanged. Separate bundle archives are no longer distributed.
