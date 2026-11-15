# Однокомандный синтетический стенд — не production

Зависит от #81 и родителей. `synthetic_stand.py` не открывает существующие базы,
не переносит реальную историю и не меняет local MVP. Все имена/учётные данные
встроенного SyntheticVerifier — публичные искусственные fixtures, не секреты
и НЕ производственная авторизация. Нельзя помещать реальные КД в этот стенд.

## Локальная автоматическая приёмка

Из checkout с установленными requirements.txt и requirements-dev.txt:

```console
python synthetic_stand.py accept --synthetic-only --workspace /workspace/work/kd-acceptance-001
```

Родительский каталог должен существовать и быть доверенным, конечный каталог —
новым. На Windows укажите новый путь под отдельным тестовым каталогом. Никакие
существующие каталоги/файлы не заменяются. Частичный стенд после ошибки остаётся
для диагностики: для повтора используйте другой новый путь, не удаляйте данные
автоматически. Без успешного process preflight каталог вообще не создаётся.

Команда создаёт две искусственные организации, несколько проектов и роли,
ScopedBackend/DurableSessions, synthetic CSV, затем через настоящий ASGI HTTP
проверяет login, движок/worker, решение и actor audit, повтор без переноса
статуса, compare, CSV/XLSX, admin config audit, structured connections, запрет
viewer write, чужих scope и logout replay. После остановки клиентов копирует
scoped SQLite в новый файл и создаёт ПУСТОЙ session store; восстановление не
клонирует старые cookies. Проверяются решения, аудит, настройки и соединения.

acceptance-result.json содержит limited synthetic_pass/failed, результаты,
Python/dependency versions, fingerprint исходников. Нет tokens, CSRF, сырых
exception, документов и содержимого базы. Fingerprint — локальная сверка,
не подпись и не криптографическая аттестация. Реальный TLS, CI и два Windows ПК
помечены not_run; production_ready всегда false. Не превращать этот JSON в
автоматическое разрешение выпуска. Если код изменился во время приёмки — отказ.

## Интерактивная проверка браузером на том же компьютере

Предоставьте отдельную тестовую TLS-пару с SAN 127.0.0.1. Закрытый ключ не
должен требовать интерактивного пароля. Доверие сертификату настраивает оператор
в тестовом окружении; launcher не меняет trust store и не отключает verification.

```console
python synthetic_stand.py serve --synthetic-only --workspace /workspace/work/kd-browser-001 --certfile TEST_CERT.pem --keyfile TEST_KEY.pem
```

Открыть https://127.0.0.1:8766/workbench. Публичные synthetic fixtures для поля
«Тестовое доказательство входа»: synthetic-engineer, synthetic-viewer,
synthetic-admin, synthetic-other-project, synthetic-other-org. Они не защищают
реальные данные и не должны переиспользоваться в enterprise verifier.

HTML формы имеют POST fallback: даже при сбое JavaScript поля не должны попадать
в URL/history. Без JavaScript операции не поддерживаются; серверная авторизация
и CSRF всё равно обязательны. Это не резервный незащищённый способ входа.

Привязка строго 127.0.0.1, host/reload/proxy overrides отсутствуют; TLS обязателен,
нет HTTP fallback. Порт 8766 отделён от local MVP 8765, можно выбрать другой
непривилегированный порт. Сертификат/ключ валидируются до создания базы.
Сервер не стартует автоматически при import или при accept. Остановка Ctrl+C.

## Проверенные и непроверенные границы

Тесты включают настоящий loopback HTTPS с отдельным временным сертификатом,
проверкой hostname и явным доверенным CA, без verify=False. Это не предприятие,
не LAN и не Windows acceptance. Наличие openssl нужно только для этого fixture;
если его нет, данный тест честно пропускается. Пользовательский serve не требует
openssl: он требует уже выданную тестовую TLS-пару.

POSIX worker budgets проверены локально; Windows Job Object требует отдельного
запуска. Нет OS filesystem/network sandbox, общих quotas нескольких workers,
подписанного manifest, production identity provider, двух физических Windows ПК,
операторского restore/cutover и разрешения LAN. До этих доказательств и ручного
review всех зависимостей стенд остаётся синтетическим. Actions пропущен [skip ci]
при неподтверждённом остатке включённого бюджета; paid runner не используется.

Совместимость с dependency guard #83: Starlette объявлен прямо (>=1.10,<2),
поскольку фабрика и middleware импортируют его API. Локально проверена 1.10.0
с FastAPI 0.162.6. Это не lockfile и не доказательство Windows resolver/CI.
