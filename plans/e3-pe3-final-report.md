# KD-PE-105: итоговый отчёт

Статус: **реализация сохранена в ветке, синтетические проверки пройдены, Draft PR создан**.
Проверка на реальных PDF Э3/ПЭ3 **не выполнена**.

## 1. Ветка и PR

- Ветка разработки: `feature/e3-pe3-missing-designations`.
- База ветки: `316832131f1a759f3a61814537f04143c9e6ca84` (`feature/recovery` в SourceCraft PR).
- SourceCraft Draft PR: **№102**, `feature/e3-pe3-missing-designations` → `feature/recovery`, статус `draft`.
  - `https://sourcecraft.dev/stammendrs1510/kd-checker/pulls/102`
- GitHub Draft PR: **не создан** — см. раздел 8.
- Автоматический merge не выполнялся. `main` не изменялся.

## 2. SHA

- База `feature/recovery`: `316832131f1a759f3a61814537f04143c9e6ca84`.
- Служебные CI-коммиты публикации `c0e4b42` и `d76ae72` в ветку **не включены**.
- SHA кода и тестов: `0ae3d3a18e01118625675773d30be6186857e4a3`.
- Последующие коммиты ветки содержат только документацию отчёта; SHA PR — в SourceCraft PR №102.
- Резервная копия рабочей копии до переноса: ветка `backup/kd-pe-105-wip` @ `750a307c9bc6d7d633c46a7d25844630b0487b0c`.
- `main`: `b97729e2451caad282240e181b959c70549a125f`, не изменялся.

## 3. Изменённые файлы

Новые:

- `e3_pe3_inventory.py`;
- `tests/test_e3_pe3_inventory.py`;
- `tests/test_e3_pe3_details.py`;
- `tests/test_e3_pe3_ui.py`;
- `docs/e3-pe3-missing-designations.md`;
- `docs/e3-pe3-details-schema.md`;
- `plans/e3-pe3-missing-designations.md`;
- `plans/draft-pr-body-e3-pe3.md`;
- `plans/e3-pe3-final-report.md`.

Изменённые:

- `kd_crosscheck.py`;
- `parser_process.py`;
- `issue_tracking.py`;
- `excel_reports.py`;
- `static/index.html`;
- `static/report.css`;
- `secure_static/workbench.js`;
- `secure_static/workbench.css`;
- `quality/rule_catalog.py`;
- `tests/test_rule_evidence_catalog.py`;
- `tests/test_pdf_normalization.py`;
- `tests/test_pe3_duplicates.py`;
- `tests/test_excel_reports.py`;
- `tests/test_issue_management.py`;
- `README.md`;
- `docs/parallel-development-acceptance-plan.md`;
- `docs/ambiguous-pdf-pairs.md`.

## 4. Алгоритм

См. [`docs/e3-pe3-missing-designations.md`](../docs/e3-pe3-missing-designations.md).

Ключевое ограничение: обозначения, которых нет в тексте Э3, **не додумываются** по количеству ПЭ3.
Расхождение количества остаётся `KD-PE-102`, а `KD-PE-105` сообщает только реально найденные в Э3
и отсутствующие в ПЭ3 обозначения.

## 5. Модель достоверности

См. раздел «Модель достоверности» в [`docs/e3-pe3-missing-designations.md`](../docs/e3-pe3-missing-designations.md)
и схему [`docs/e3-pe3-details-schema.md`](../docs/e3-pe3-details-schema.md).

## 6. Фактический результат `KD-PE-105`

Синтетический контрольный пример: Э3 — 9 трансформаторов `1TA1`…`3TA3`;
ПЭ3 — `1TA1...1TA3; 2TA1...2TA3; 3TA1`, количество 9.

Фактически получено:

- `KD-PE-102` — 9 против 7 раскрытых обозначений;
- `KD-PE-105` — `warning`, `details.status = evaluated`, `reason = quantity_mismatch`;
- `details.missing` — `3TA2`, `3TA3`;
- `details.pe3_quantity = 9`, `details.pe3_entries_count = 7`;
- `details.pe3_page` и `details.pe3_table_row` заполнены;
- `details.e3_pages = [1, 2]`;
- при исправленном ПЭ3 `3TA1...3TA3` `KD-PE-105` и `KD-PE-102` исчезают.

## 7. Тесты

| Показатель | Фактически |
|---|---|
| Новые тесты `test_e3_pe3_inventory.py`, `test_e3_pe3_details.py`, `test_e3_pe3_ui.py` | **38 passed** |
| Полный `python -m pytest -q` | **954 passed, 1 skipped** |
| Пропуск | `tests/test_parser_process.py:89` — Windows Job Object bootstrap only |
| Целевая совместимость KD-PE-100—104 / schema v1 / история / Excel / CSV | **162 passed** |
| Ошибки/падения | **0 failed** |

Дополнительная фактическая проверка совместимости:

- история сохраняет и возвращает `details` без миграции SQLite;
- CSV-экспорт — ровно 6 колонок, `details` в CSV не добавляются;
- Excel без `details` — 10 колонок; с `details` — 12 колонок (K/L).

### Исправление в ходе проверки

Первый прогон выявил 1 падение нового теста и 2 падения базовых регрессий после неудачной попытки
«додумывать» недостающие обозначения по количеству ПЭ3. Такое поведение противоречит базовым тестам
`test_incomplete_source_is_not_invented_to_match_quantity` и `test_finding_severity`.
Вывод откачен; модуль побайтово совпадает с подготовленной версией. В тесте
`test_similar_groups_are_not_confused` исправлена только фикстура: обозначения `3TA2`, `3TA3`
теперь реально присутствуют в Э3, а утверждение о их попадании в группу 3 сохранено.
Существующие тесты не ослаблялись.

## 8. CI

| CI | Фактически |
|---|---|
| Linux (локальный прогон) | выполнен: 954 passed, 1 skipped |
| Windows | не запускался |
| GitHub Actions Windows/Linux на PR | **не запускался** |
| SourceCraft CI на PR | **не запускался** |

Причина GitHub Actions: [`.github/workflows/tests.yml`](../.github/workflows/tests.yml) реагирует
только на `pull_request` в `main`. PR в `feature/recovery` workflow не запускает.

Причина SourceCraft CI: [`.sourcecraft/ci.yaml`](../.sourcecraft/ci.yaml) в базе `3168321`
отсутствует (добавлен служебными коммитами публикации, которые исключены из ветки), а
существующий workflow в `feature/recovery` нацелен на push в `feature/recovery` и публикацию
фиксированного SHA `3168321` в GitHub, а не на тесты PR.

Без изменения `main` и без платных runners безопасная проверка: локальный Linux-прогон (выполнен)
и ручной запуск GitHub Actions на отдельном тестовом PR в `main` только после согласования.

## 9. GitHub Draft PR

GitHub Draft PR **не создан**. Технические причины:

- `gh` CLI не установлен;
- в окружении отсутствует `GH_TOKEN`/`GITHUB_TOKEN`;
- существующий mirror workflow публикует фиксированный SHA `3168321` в GitHub `feature/recovery`
  и открывает PR в `main`, а не ветку `feature/e3-pe3-missing-designations` в `feature/recovery`.

Изменять mirror workflow ради этого в рамках задачи нельзя: это затронуло бы служебную
публикационную цепочку и потенциально GitHub-ветки. Требуется отдельный токен и явное решение.

## 10. Ограничения

- Нет OCR; текстовый слой обязателен.
- `KD-PE-105` не доказывает физическое отсутствие элемента.
- Одна ссылка на вывод не даёт warning.
- Клеммные семейства — только `info`.
- Наименование ПЭ3 — best-effort.
- 100 страниц PDF — общий лимит.
- Реальные PDF предприятия не добавлены в репозиторий.
- Проверка на реальных PDF Э3/ПЭ3 **не выполнена**; независимая приёмка на Windows 11 остаётся за заказчиком.

## 11. Блокеры и незакрытые пункты

1. GitHub Actions Windows/Linux не запускались на PR в `feature/recovery` из-за фильтра workflow.
2. GitHub Draft PR не создан из-за отсутствия токена и `gh` CLI.
3. Проверка на реальных PDF не выполнена: файлы доступны заказчику, но не в SourceCraft.
