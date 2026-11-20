# Recovery: интеграция Phase 0–6 (53 PR) в `feature/recovery`

Draft PR. **Merge не выполняется** до независимой приёмки.
База: `main` = `b97729e2451caad282240e181b959c70549a125f`.
Ветка: `feature/recovery`.

## Что внутри

Интеграционная ветка собрана из 53 восстановленных PR по шести фазам.
Порядок вливания, merge-коммиты и разрешённые конфликты описаны в
[`plans/recovery-progress.md`](plans/recovery-progress.md), полная
инвентаризация — в [`plans/pr-recovery-audit.md`](plans/pr-recovery-audit.md).

| Фаза | Содержание | PR |
|------|------------|----|
| 0 | git-гигиена, pytest-конфигурация, базовый аудит | — (`chore/stabilization-bootstrap`) |
| 1 | качество, CI-политика, документация | #83, #72, #46, #57, #38, #37, #48, #39, #23, #44 |
| 2 | парсеры и правила обозначений | #40, #41, #82, #54, #88, #90 |
| 3 | история, аудит, staging | #45, #55, #56, #75, #76, #77, #58, #64, #66 |
| 4 | secure core: API, сессии, backend | #52, #59, #60, #61, #62, #63, #74 |
| 5 | соединения, проектные настройки, лимиты тела | #47, #65, #67, #68, #69, #70, #71, #73, #78, #79 |
| 6 | рабочее место, изоляция парсеров, приёмка | #80, #81, #84, #85, #86, #87, #92, #93, #94, #89, #91 |

Вне ветки осознанно оставлены #42, #43, #49, #50, #51, #53 и Render-демо
#95–#98.

## Восстановленные артефакты Phase 0

В ветке отсутствовали файлы, зафиксированные в
`chore/stabilization-bootstrap` (`6a652cc`). Добавлены:

- `.gitignore`, `.gitattributes` — исключение `.env`, локальных БД,
  `data/`, `__pycache__`, `.venv`, артефактов Playwright;
- `pyproject.toml` — `testpaths`, `pythonpath`, маркер `ui`;
- `docs/stabilization-baseline.md`;
- `plans/pr-recovery-audit.md`, `plans/stabilization-roadmap.md`.

Одно отличие от исходного `6a652cc`: `testpaths = ["tests", "test_free_cycle.py"]`.
Корневой файл содержит 7 тестов планировщика и без явного указания выпадал
бы из штатного прогона. `tests/test_engine.py` очищен от ручного `sys.path`
(покрывается `pythonpath = ["."]`).

## Проверки на текущем HEAD

| Проверка | Результат |
|----------|-----------|
| `python tools/dependency_policy.py` | passed |
| `python -m pytest -q` | **914 passed, 1 skipped, 1 warning** |
| `python -m pytest -q --ignore=tests/test_issue_ui.py` | **909 passed, 1 skipped, 1 warning** |
| Браузерные `test_issue_ui.py` + `test_secure_workbench.py` | 7 passed |
| Импорт всех модулей Phase 4–6 и `quality/*` | ok |
| SHA-256 `storage.py` / `config.json` / `app.py` | `4bb40ce…` / `1c137bc…` / `de17ec3…` — совпадают с Phase 3–6 |

Единственный skip: `tests/test_parser_process.py:89` — Windows Job Object
bootstrap, вне Linux-окружения.

## Ограничения и открытые вопросы

1. CI GitHub Actions на этой ветке ранее не запускался; зелёный прогон
   ожидается в рамках этого Draft PR.
2. `StarletteDeprecationWarning` про `httpx`/`httpx2` — существующее
   предупреждение зависимости, не дефект PR.
3. `docs/checklist-session-renewal-design.md` — design-only (#21),
   runtime не реализован.
4. Render-демо #95–#98 не входят в ветку и не проверялись.
5. EPLAN Checklist, Supabase, RLS и производственные данные не менялись;
   внешние вызовы в тестах не выполняются.

## Границы изменений

`main`, Supabase, EPLAN Checklist и производственные данные не изменяются.
Merge, публикация PR и любые операции с `main` — только после независимой
приёмки.
