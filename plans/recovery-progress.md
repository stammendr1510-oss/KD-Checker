# Прогресс восстановления `feature/recovery`

Рабочий журнал интеграционной ветки. План и полная инвентаризация 63 PR —
[`plans/pr-recovery-audit.md`](pr-recovery-audit.md) (ветка
`chore/stabilization-bootstrap`).

База: `main` = `b97729e2451caad282240e181b959c70549a125f`.
Ветка: `feature/recovery`.

## Phase 0 — подготовка (выполнено)

| Шаг | Результат |
|-----|-----------|
| Локальные артефакты стабилизации | зафиксированы в ветке `chore/stabilization-bootstrap` (`6a652cc`): `.gitignore`, `.gitattributes`, `pyproject.toml`, `plans/`, `docs/stabilization-baseline.md`, удалён ручной `sys.path` в `tests/test_engine.py` |
| `main` | не изменялся |
| `feature/recovery` | создана от `b97729e` и опубликована |
| CI на push | намеренно **не** добавлен: правка `tests.yml` конфликтует с #83/#72; проверки выполняются локально на чекпоинтах |
| Квота Actions | не подтверждена; PR с `[skip ci]` (#80, #81, #84–#87) по-прежнему без зелёного CI |

## Phase 1 — качество, CI, документация (выполнено)

Влиты: **#83, #72, #46, #57, #38, #37, #48, #39, #23, #44**.

Чекпоинт: `python tools/dependency_policy.py` — passed;
77 целевых тестов (`test_api_access_inventory`, `test_rule_evidence_catalog`,
`test_dependency_policy`, `test_checklist_session_design`,
`test_access_policy_design`) — passed.

## Phase 2 — парсеры и правила обозначений (выполнено)

Влиты: **#40, #41, #82, #54, #88, #90**.

- #41: ожидаемый конфликт в `extractors.py` снят merge-коммитом
  `ae4bed4` на ветке `fix/bounded-pdf-page-iteration`; сохранены оба
  изменения (CSV `islice` + PDF `islice`), дубля импорта нет.
- #88 перенацелен с ветки #82 на `feature/recovery`.
- #90 заменяет `start_windows.bat`; при выполнении итерации 8 плана
  стабилизации лаунчер будет переписан — учесть при ревью.

Чекпоинт: `.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py` —
**478 passed, 1 warning**; `dependency_policy` — passed.

Браузерный модуль `tests/test_issue_ui.py` по-прежнему падает на 5 тестах
из-за отсутствия Chromium (находка F3 плана стабилизации), к merge-ам
отношения не имеет.

## Phase 3 — история и staging (выполнено)

Порядок вливания: **#45 → #55 → #56 → #75 → #76 → #77 → #58 → #64 → #66**
(все 9 PR перенацелены на `feature/recovery`, merge-коммиты без squash):

| PR | Ветка | Merge-коммит |
|----|-------|--------------|
| #45 | `feature/verified-history-copy` | `749c8ab` |
| #55 | `feature/scoped-history-repository` | `827057f` |
| #56 | `feature/staging-history-import` | `0107ccf` |
| #75 | `test/staging-import-adversarial-integrity` | `88cbcac` |
| #76 | `quality/staging-import-readonly-preflight` | `a789661` |
| #77 | `quality/staging-row-reconciliation` | `7bac846` |
| #58 | `test/scoped-history-multiprocess-acceptance` | `9b48193` |
| #64 | `test/scoped-audit-recovery-matrix` | `b982e94` |
| #66 | `quality/scoped-audit-readonly-verifier` | `1b7e649` |

- Stacked-ветки #56, #75, #76, #77, #58, #64, #66 перенацелены на
  `feature/recovery` до merge; черновики #75, #76, #77, #58, #64, #66
  опубликованы. Ветки-источники не удалялись.
- Текстовых конфликтов не возникло: merge-checks чистые, локальный
  fast-forward `c82d879 → 1b7e649` без merge-конфликтов.
- Локальный MVP не затронут: SHA-256 `storage.py`, `config.json`, `app.py`
  до и после Phase 3 совпадают
  (`4bb40ce…`, `1c137bc…`, `de17ec3…`).

Чекпоинт: `python tools/dependency_policy.py` — passed;
`.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py` —
**570 passed, 1 warning** (Starlette/httpx), рост с 478 за счёт новых
тестов истории/staging.

Целостность SQLite на синтетических мигрированных схемах:

| Схема | `integrity_check` | `foreign_key_check` | Дополнительно |
|-------|-------------------|---------------------|---------------|
| `storage.py` (legacy + решения/аудит) | `ok` | нарушений нет | 2 run |
| `scoped_history.py` (scoped + транзакции) | `ok` | нарушений нет | `scoped_audit_verifier`: ok |
| verified backup (#45) | `ok` | нарушений нет | — |
| staging import (#56) | `ok` | нарушений нет | preflight и row reconciliation: ok, `backup_checked=true` |

## Phase 4 — secure core (выполнено)

Порядок вливания: **#52 → #59 → #60 → #61 → #62 → #63 → #74**
(все 7 PR перенацелены на `feature/recovery`, merge-коммиты без squash):

| PR | Ветка | Merge-коммит |
|----|-------|--------------|
| #52 | `feature/secure-api-boundary` | `ad3ad9e` |
| #59 | `feature/secure-real-backend` | `7e6aa2b` |
| #60 | `feature/durable-test-sessions` | `db171b4` |
| #61 | `test/secure-e2e-revocation-acceptance` | `cd7fff5` |
| #62 | `test/secure-http-adversarial-uploads` | `42145a0` |
| #63 | `test/durable-sessions-failclosed-matrix` | `4ac714a` |
| #74 | `quality/durable-session-offline-preflight` | `7677406` |

- #52 и #59 были открыты; черновики #61, #62, #63, #74 опубликованы после
  retarget на `feature/recovery` и до merge. Ветки-источники не удалялись.
- Merge-checks у всех 7 PR чистые, текстовых конфликтов не возникло.
- #59 не импортирует `connection_checks`/`KD-CON`, поэтому зависимость от
  #47 на этом этапе не требуется: #47 остаётся в Phase 5 вместе с #65.
- Локальный MVP не затронут: SHA-256 `storage.py`, `config.json`, `app.py`
  до и после Phase 4 совпадают
  (`4bb40ce…`, `1c137bc…`, `de17ec3…`).

Новые модули Phase 4: `secure_api.py`, `secure_sessions.py`,
`secure_backend.py`, `durable_sessions.py`,
`quality/durable_session_preflight.py`.

На чекпоинте `tools/dependency_policy.py` сначала обнаружил
`undeclared-runtime-import:starlette` из-за прямого импорта в
`secure_api.py`. Исправлено без новой зависимости: middleware берётся через
реэкспорт `fastapi.middleware.trustedhost`; после правки политика проходит.

Чекпоинт:

- `.venv/bin/python tools/dependency_policy.py` — **passed**.
- `.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py` —
  **721 passed, 1 warning** (StarletteDeprecationWarning про `httpx`/`httpx2`),
  рост с 570 за счёт HTTP/RBAC, durable-сессий и preflight-тестов.
- Импорт всех новых модулей Phase 4 — **OK** (включая
  `quality.durable_session_preflight`).

## Phase 5 — соединения, проектные настройки и лимиты тела (выполнено)

Порядок вливания: **#47 → #65 → #67 → #68 → #69 → #70 → #71 → #73 → #78 → #79**
(все 10 PR перенацелены на `feature/recovery`, merge-коммиты без squash):

| PR | Ветка | Merge-коммит |
|----|-------|--------------|
| #47 | `feature/structured-connection-checks` | `80c24eb` |
| #65 | `feature/protected-connection-import` | `64cacee` |
| #67 | `test/connection-api-adversarial-acceptance` | `35a7ca0` |
| #68 | `feature/project-config-audit` | `8442a74` |
| #69 | `feature/secure-body-budget` | `be83063` |
| #70 | `test/config-version-history-invariants` | `68ac95c` |
| #71 | `test/body-budget-boundary-matrix` | `a7ad23b` |
| #73 | `quality/project-config-readonly-audit-verifier` | `68e084d` |
| #78 | `test/two-instance-http-consistency` | `0e396fe` |
| #79 | `test/tenant-scope-http-replay-matrix` | `da8d936` |

- #47 влит первым: #65 зависит от `connection_checks`/KD-CON, поэтому без
  него цепочка Phase 5 была бы неполной (см. примечание Phase 4).
- Draft-ветки #67, #70, #71, #73, #78, #79 опубликованы после retarget на
  `feature/recovery` и до merge. Ветки-источники не удалялись.
- Merge-checks у всех 10 PR чистые; текстовых конфликтов не возникло;
  локальный fast-forward `a3772c7 → da8d936` без конфликтов.
- Локальный MVP не затронут: SHA-256 `storage.py`, `config.json`, `app.py`
  до и после Phase 5 совпадают
  (`4bb40ce…`, `1c137bc…`, `de17ec3…`).

Новые модули Phase 5: `connection_checks.py`, `connection_import.py`,
`body_budget.py`, `project_configuration.py`,
`quality/project_config_audit_verifier.py`.

На чекпоинте `tools/dependency_policy.py` обнаружил
`undeclared-runtime-import:starlette` из-за прямого импорта в
`body_budget.py`. Исправлено без новой зависимости: `Response` берётся через
реэкспорт `fastapi.responses` (класс идентичен
`starlette.responses.Response`); после правки политика проходит.

Чекпоинт:

- `.venv/bin/python tools/dependency_policy.py` — **passed**.
- `.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py` —
  **832 passed, 1 warning** (StarletteDeprecationWarning про `httpx`),
  рост с 721 за счёт тестов соединений, проектных настроек, body budget,
  двух инстансов и tenant scope.
- Импорт всех новых модулей Phase 5 — **OK** (включая `connection_checks`,
  `connection_import`, `body_budget`, `project_configuration`,
  `quality.project_config_audit_verifier`).

## Phase 6 — рабочее место, изоляция парсеров и приёмка (выполнено)

Порядок вливания: **#80 → #81 → #84 → #85 → #86 → #87 → #92 → #93 → #94 → #89 → #91**
(все 11 PR перенацелены на `feature/recovery`, черновики опубликованы до merge,
merge-коммиты без squash):

| PR | Ветка | Merge-коммит |
|----|-------|--------------|
| #80 | `feature/russian-secure-workbench` | `3039530` |
| #81 | `feature/parser-process-boundary` | `13a9c5e` |
| #84 | `feature/synthetic-acceptance-stand` | `4a1c9d8` |
| #85 | `feature/run-provenance-snapshot` | `5244355` |
| #86 | `feature/shared-parser-capacity` | `0ad4c19` |
| #87 | `feature/parser-disconnect-cancellation` | `f4b8aa1` |
| #92 | `fix/windows-parser-worker-bootstrap` | `f613f6d` |
| #93 | `test/kd-parser-worker-pdf-bundle` | `1d936cf` |
| #94 | `feature/kd-pe-100-evidence-v1` | `a0527ea` |
| #89 | `test/kd-offline-pdf-acceptance` | `aca723f` |
| #91 | `docs/kd-offline-acceptance` | `c4406b1` |

- Ветки с ранее пропущенным CI (#80, #81, #84–#87) проверены локально на
  интеграционной ветке; `[skip ci]` больше не является основанием считать их
  непроверенными.
- Render-демо #95–#98 не трогались и остаются вне `main`.

### Разрешённые конфликты и проблемы

1. **#87 ↔ #69/Phase 5, `body_budget.py`.** PR #87 переписывает
   `BodyBudget` для наблюдения transport disconnect и импортирует `Response`
   напрямую из `starlette.responses`; в `feature/recovery` уже был фикс
   Phase 5 с реэкспортом `fastapi.responses`. Конфликт снят merge-коммитом
   `ff00fa6` на ветке `feature/parser-disconnect-cancellation`: сохранена
   логика watcher/cancellation из #87, импорт оставлен через
   `fastapi.responses` (класс идентичен), затем PR влит как `f4b8aa1`.
   Целевой набор `test_body_budget*` + `test_parser_disconnect` — 30 passed.
2. **#94 ↔ #87, сигнатура `_validate_result`.** #94 расширил функцию до
   `_validate_result(value, result_version=1, known_files=())`, а тест #87
   подменял её функцией с одним аргументом. Исправлено отдельным коммитом
   `fc66bc7` в `feature/recovery`: обёртка пробрасывает `*args/**kwargs`.
   Runtime-код PR не изменялся.
3. **F3 — браузерные тесты.** `tests/test_secure_workbench.py` требует
   Playwright Chromium. В окружении браузер и системные библиотеки
   отсутствовали; установлены `playwright install chromium` и
   `playwright install-deps chromium`. После этого браузерный сценарий
   зелёный. Это инфраструктурная предпосылка, а не дефект PR.

### Чекпоинты по блокам

| Блок | Состав | Результат |
|------|--------|-----------|
| 1 | #80, #81, #84 | `dependency_policy` — passed; **856 passed, 1 warning** |
| 2 | #85, #86, #87 | `dependency_policy` — passed; **887 passed, 1 warning** |
| 3 | #92, #93, #94, #89, #91 | `dependency_policy` — passed; **909 passed, 1 skipped, 1 warning** |

Финальный прогон: `.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py`
— **909 passed, 1 skipped, 1 warning** (существующее
StarletteDeprecationWarning про `httpx`); skipped — optional staging-тест #56.
`tools/dependency_policy.py` — **passed** (прямых недекларированных импортов
нет; `starlette` объявлен в #84, но `body_budget.py` продолжает использовать
реэкспорт FastAPI).

Локальный MVP не затронут: SHA-256 `storage.py`, `config.json`, `app.py`
до и после Phase 6 совпадают
(`4bb40ce…`, `1c137bc…`, `de17ec3…`).

Новые модули/артефакты Phase 6: `parser_process.py`, `synthetic_stand.py`,
`secure_static/workbench.*`, `source_identity.py`, `pdf_match_evidence.py`,
`docs/parser-process-boundary.md`, `docs/parser-disconnect-cancellation.md`,
`docs/shared-parser-capacity.md`, `docs/run-provenance.md`,
`docs/secure-workbench.md`, `docs/synthetic-acceptance-stand.md`,
`docs/kd-offline-acceptance.md`.

## Правила чекпоинта

1. После каждого блока — `python tools/dependency_policy.py`.
2. Полный прогон `pytest -q --ignore=tests/test_issue_ui.py`.
3. `git status --porcelain` не должен содержать изменённых runtime-файлов.
4. Merge только без `squash` для stacked-цепочек; ветки-источники не удалять
   до завершения цепочки.
5. Для браузерных тестов заранее установить Playwright Chromium и системные
   зависимости (`playwright install chromium`, `playwright install-deps chromium`).
6. При stacked-merge проверять сигнатуры monkeypatch-обёрток: поздние PR могут
   расширять внутренние функции, которые подменяют тесты ранних PR.
