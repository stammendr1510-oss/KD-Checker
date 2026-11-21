# KD-PE-105, блок 2: итоговый отчёт

Статус: **реализация и синтетические проверки завершены**.
Проверка на реальных PDF Э3/ПЭ3 не выполнялась — файлы недоступны в окружении.

## 1. Ветка и база

- Рабочая ветка блока 1: `diagnostics/kd-pe-105-baseline`
  (`be3b368`, поверх `a968ef8`).
- Ветка блока 2: `feature/kd-pe-105-matching-evidence`
  (создана от `diagnostics/kd-pe-105-baseline`).
- Draft PR: создаётся в SourceCraft, база — `diagnostics/kd-pe-105-baseline`
  (stacked PR, блок 2 не смешивается с диагностикой блока 1).
- `main`, production, Supabase и EPLAN Checklist не изменялись.

## 2. Цель блока 2

Устранить дефект KM: семейство из 17 обозначений больше не может автоматически
считаться доказанно сопоставленным строке ПЭ3 с количеством 1. Одновременно
сохраняется обнаружение `3TA2`/`3TA3` в контрольном случае TA.

## 3. Изменённые файлы

Изменены:

- `kd_crosscheck.py` — разбор ячеек ПЭ3: явные разделители `;`/`,`, переносы
  только как продолжение диапазона, сохранение двусмысленного фрагмента в `unknown`;
- `e3_pe3_inventory.py` — доказательное сопоставление строк, новые причины,
  независимая от severity достоверность, новые инварианты `valid_details()`;
- `tests/test_kd_pe_105_diagnostics.py` — диагностические тесты блока 1 переведены
  на исправленное поведение;
- `docs/e3-pe3-missing-designations.md` — алгоритм, доказательства, warning gate;
- `README.md` — краткое описание нового поведения.

Добавлены:

- `tests/test_e3_pe3_row_matching.py` — 16 тестов блока 2;
- `docs/kd-pe-105-matching-evidence.md` — подробная модель доказательств;
- `plans/kd-pe-105-block2-report.md` — этот отчёт.

## 4. Алгоритм

1. Точное совпадение: обозначение или база контакта есть в `entries` — замечания нет.
2. Для отсутствующего обозначения строки того же семейства классифицируются:
   - `under_expanded` — `quantity > len(entries)` и группа совместима;
   - `family_group` — совпали тип и группа, покрытие не доказано;
   - `family_only` — совпал только буквенный префикс;
   - `none` — подходящих строк нет.
3. Ровно одна `under_expanded` → подтверждённая строка, `quantity_mismatch`.
4. Несколько `under_expanded` → `row_ambiguous`, строка не выбирается.
5. Ровно одна `family_group` → `row_matched_without_coverage`, только info.
6. Несколько `family_group` → `row_ambiguous`, строка не выбирается.
7. Иначе → `no_matching_pe3_row`.
8. `pe3_page`/`pe3_table_row` заполняются только для подтверждённой или
   единственной предположительной строки.

## 5. Warning gate

Warning возможен только при уникальной строке `under_expanded`,
`quantity > len(entries)`, `reason=quantity_mismatch`, достоверности
`high`/`medium`, непустом семействе не клеммного типа и отсутствии
`partial`/`truncated`.

`valid_details()` отклоняет:

- warning с причиной, отличной от `quantity_mismatch`;
- `row_ambiguous` с `confidence != low`;
- `row_ambiguous`/`no_matching_pe3_row` с заполненной строкой ПЭ3;
- `row_matched_without_coverage` с `confidence=high`;
- устаревшую причину `ambiguous_row`.

## 6. Контрольные случаи

### A. TA

Э3: `3TA1`, `3TA2`, `3TA3`.
ПЭ3: `1TA1...1TA3; 2TA1...2TA3; 3TA1`, количество 9.

Результат: `warning`, `reason=quantity_mismatch`, `confidence=high`,
`missing=[3TA2, 3TA3]`, лист и строка ПЭ3 заполнены.

### B. KM

Э3: 17 обозначений `3KM2`…`3KM18`.
ПЭ3: строка `3KM1`, количество 1.

Результат: `info`, `reason=row_matched_without_coverage`, `confidence=medium`,
`missing` содержит ровно 17 найденных обозначений. Warning невозможен.

### C. Одинаковые типы

Две строки `3KM1`/`3KM2` с похожими наименованиями, отсутствует `3KM3`.

Результат: `info`, `reason=row_ambiguous`, `confidence=low`,
`pe3_page=null`, `pe3_table_row=null`. Строки не объединяются.

## 7. Тесты

Целевой прогон:

```text
.venv/bin/python -m pytest -q \
  tests/test_e3_pe3_row_matching.py \
  tests/test_kd_pe_105_diagnostics.py \
  tests/test_e3_pe3_inventory.py \
  tests/test_e3_pe3_details.py \
  tests/test_pdf_normalization.py \
  tests/test_crosscheck.py
```

Результат: все тесты проходят (см. фактический полный прогон ниже).

Полный прогон:

```text
.venv/bin/python -m pytest -q
990 passed, 1 skipped, 1 warning in 100.71s
```

- Skip: `tests/test_parser_process.py:89` — Windows Job Object bootstrap only.
- Warning: Starlette/httpx deprecation, не связан с изменениями.
- Новых необъяснённых ошибок нет.
- База блока 1: `974 passed, 1 skipped`; добавлено 16 тестов блока 2 → `990 passed`.

## 8. Совместимость

- `KD-PE-100`–`KD-PE-104` не изменялись.
- Схема `details` v1 не расширялась полями; добавлены только значения `reason`.
- Шесть обязательных полей замечания не менялись.
- CSV — строго 6 колонок; Excel — 10 колонок без `details`, 12 с `details`.
- SQLite-схема и история не менялись.
- `parser_process._validate_result()` принимает `details` только для `KD-PE-105`.

## 9. Ограничения и блокеры

1. Реальные PDF `ТНБВ.656451.162.Э3`/`ПЭ3` и XLSX-отчёт недоступны в окружении.
   Приёмка на реальных документах остаётся ручным шагом заказчика.
2. Windows/Linux CI на stacked PR в `diagnostics/kd-pe-105-baseline` может не
   запускаться: GitHub workflow реагирует только на PR в `main`. Это ограничение
   инфраструктуры, а не результата тестов.
3. `KD-PE-105` не является OCR и не доказывает физическое отсутствие элемента.
4. Наименование ПЭ3 — best-effort.
5. Клеммные семейства остаются только `info`.

## 10. Критерии приёмки блока 2

- [x] Точное и предположительное сопоставление разделены.
- [x] Совпадение буквенного префикса не считается доказательством.
- [x] Несколько кандидатов сохраняются как `row_ambiguous`, строка не выбирается.
- [x] Количество используется как дополнительное свидетельство.
- [x] Числовой пробел не порождает обозначения.
- [x] `pe3_page`/`pe3_table_row` заполняются только при определённой строке.
- [x] Причины несопоставления явно записываются в `reason` и `message`.
- [x] Случай A (`3TA2`, `3TA3`) не регрессировал.
- [x] Случай B (17 KM) не даёт warning/high.
- [x] Случай C (похожие типы) не объединяет строки.
- [x] Тесты на ноль/одну/несколько подходящих строк добавлены.
- [x] Полный pytest: `990 passed, 1 skipped`.
- [x] `KD-PE-100`–`KD-PE-104` совместимы.
- [x] `main` и production не изменены.
