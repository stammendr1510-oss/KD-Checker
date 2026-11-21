# KD-PE-105: позиционные обозначения Э3, отсутствующие в ПЭ3

## База и ветка

- База: `feature/recovery` @ `316832131f1a759f3a61814537f04143c9e6ca84`.
- Ветка: `feature/e3-pe3-missing-designations` @ `0ae3d3a18e01118625675773d30be6186857e4a3`.
- SourceCraft Draft PR: №102, `https://sourcecraft.dev/stammendrs1510/kd-checker/pulls/102`.
- GitHub PR не создавался: нет `gh`/токена, а mirror workflow публикует фиксированный SHA в `feature/recovery` и PR в `main`.

## Что изменено

- Новый модуль `e3_pe3_inventory.py`: контекстная классификация упоминаний Э3, модель достоверности, сопоставление с раскрытыми обозначениями ПЭ3, `details` schema v1 и `valid_details()`.
- `kd_crosscheck.py`: ограниченный обход 100 страниц, сбор упоминаний, `name`/`page_size_pt`/`pdf_truncated`, интеграция `KD-PE-105`.
- `parser_process.py`: allowlist `details` только для `KD-PE-105`, строгая валидация.
- `issue_tracking.py`: fingerprint учитывает канонический `details` только при наличии.
- `excel_reports.py`: существующий лист `Замечания`; при наличии `details` добавляются только колонки K/L.
- `static/index.html`, `static/report.css`, `secure_static/workbench.js`, `secure_static/workbench.css`: блок структурированных сведений.
- `quality/rule_catalog.py`: регистрация `KD-PE-105`.
- Тесты: `tests/test_e3_pe3_inventory.py`, `tests/test_e3_pe3_details.py`, `tests/test_e3_pe3_ui.py`; обновлены контрольные регрессии.
- Документация: `docs/e3-pe3-missing-designations.md`, `docs/e3-pe3-details-schema.md`, README и матрица приёмки.

## Алгоритм

1. Э3: слова с координатами, контекст `label`/`split_label`/`reference`/`note`/`table`.
2. Нормализация латинских/кириллических двойников, базовое обозначение для `.N`.
3. ПЭ3: существующий `get_spec_rows`, диапазоны `...`, `…`, `;`, переносы, несколько групп.
4. Сравнение множеств; привязка отсутствующих к строке ПЭ3 по семейству/группе.
5. `warning` только при достаточных доказательствах; иначе `info`/`not_evaluated`.

## Контрольный пример

Э3: 9 трансформаторов `1TA1`…`3TA3`.
ПЭ3: `1TA1...1TA3; 2TA1...2TA3; 3TA1`, количество 9.

Ожидается:

- `KD-PE-102` — 9 против 7;
- `KD-PE-105` — warning с `3TA2`, `3TA3`;
- `details.missing` — `3TA2`, `3TA3`;
- `details.pe3_quantity=9`, `details.pe3_entries_count=7`;
- листы Э3 1, 2; лист/строка ПЭ3.

При исправленном ПЭ3 `3TA1...3TA3` оба замечания по группе TA исчезают.

## Совместимость

- Шесть обязательных полей замечания не меняются.
- `details` добавлен только к `KD-PE-105`.
- SQLite-схема не меняется; старые записи читаются без миграции.
- CSV строго 6 колонок.
- Excel сохраняет лист `Замечания` и 10 базовых колонок; K/L добавляются только при наличии `details`.
- `KD-PE-100`–`KD-PE-104` не заменяются.
- Изоляция parser worker и лимиты не ослаблены.

## Проверки (фактически выполнены)

| Проверка | Результат |
|---|---|
| Целевые тесты | 38 passed |
| Полный `python -m pytest -q` | 954 passed, 1 skipped (Windows-only Job Object) |
| KD-PE-100—104 / schema v1 / история / Excel / CSV | 162 passed; CSV 6 колонок, Excel 10 → 12 колонок только при `details` |
| Windows CI | не запускался: workflow реагирует только на PR в `main` |
| Linux CI | не запускался по той же причине; локальный Linux-прогон выполнен |
| Реальные PDF | не предоставлены в SourceCraft; независимая приёмка на Windows 11 остаётся за заказчиком |

Незакрытые пункты: GitHub Actions Windows/Linux и GitHub Draft PR (см. итоговый отчёт).

## Границы

`main`, Supabase, EPLAN Checklist и production-данные не изменяются. Реальные PDF предприятия в репозиторий не добавляются. Платные API и сервисы не используются. Merge не выполняется.
