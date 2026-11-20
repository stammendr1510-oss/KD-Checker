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

## Phase 4–6 — после Phase 3

- Phase 4: #52 → #59 → #60 → #61 → #62 → #63 → #74.
- Phase 5: #65 → #67 → #68 → #69 → #70 → #71 → #73 → #78 → #79.
- Phase 6: #80 → #81 → #84 → #85 → #86 → #87 → #92 → #93 → #94 → #89 → #91.
- Render-демо #95–#98 — вне `main`.

Перед каждым merge stacked-ветки обязателен retarget на `feature/recovery`
и повторный merge-check: часть PR до сих пор нацелена на родительские ветки.

## Правила чекпоинта

1. После каждого блока — `python tools/dependency_policy.py`.
2. Полный прогон `pytest -q --ignore=tests/test_issue_ui.py`.
3. `git status --porcelain` не должен содержать изменённых runtime-файлов.
4. Merge только без `squash` для stacked-цепочек; ветки-источники не удалять
   до завершения цепочки.
