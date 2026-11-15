# Durable sessions для синтетического стенда

Зависит от #59 и его зависимостей. DurableSessions.create(NEW_PATH) создаёт
отдельную SQLite; существующий файл не перезаписывается. Повторное открытие
сохраняет сессии после перезапуска. Только хеши случайных session/CSRF tokens,
identity metadata и сроки хранятся в таблице. Это не шифрование identity.

BEGIN IMMEDIATE сериализует issue/touch/revoke. Revoke удаляет запись для всех
процессов; touch никогда не создаёт её заново. Expiry/idle проверяются по
сохранённым значениям, при откате часов до last_seen сессия отклоняется.
Используются wall-clock seconds, поскольку monotonic нельзя сохранять между
перезагрузками. Требуются управляемые системные часы и одинаковая конфигурация.

Тесты: повторное открытие, spawn-process revoke, hash-only storage, idle/absolute
expiry, конкурентные touch/revoke и настоящий HTTP logout между двумя экземплярами
приложения с одним хранилищем. Операции KD и членство остаются в scoped repository.

Не PostgreSQL, не distributed store и не SQLite на сетевом диске. Нет production
identity provider, rate limiter, шифрования/retention и общей транзакции session
revocation с уже выполняющейся бизнес-операцией. Последующий запрос запрещается;
уже авторизованную выполняющуюся операцию автоматически отменить нельзя.
Сервер не запускается в LAN. Синтетические ticket verifier не production login.
