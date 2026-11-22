# KD-PE-105, блок 4: итоговый отчёт

Статус: **реализация, синтетические проверки и полный pytest завершены**.
Проверка на реальных PDF Э3/ПЭ3 не выполнялась — файлы недоступны в окружении.
Блок 5 не начинался.

## 1. Цель блока 4

Исправить назначение `confidence` и `severity`, чтобы KD-PE-105 не создавал
ложное ощущение доказанности инженерной ошибки. Исходный дефект: замечание №7
по KM имело одновременно `reason=ambiguous_row`, `confidence=high` и уровень
«Предупреждение» без независимых доказательств.

Блок 4 вводит единую модель, в которой итоговая достоверность не превышает
самое слабое обязательное доказательство, а `reason`, `confidence` и уровень
замечания проверяются на совместимость.

## 2. Изменённые и добавленные файлы

Добавлены:

- [`kd_pe_105_confidence.py`](../kd_pe_105_confidence.py) — централизованная
  модель доказательств, агрегации и таблицы решений;
- [`tests/test_kd_pe_105_confidence_model.py`](../tests/test_kd_pe_105_confidence_model.py)
  — 41 позитивный и негативный тест;
- [`docs/kd-pe-105-confidence-model.md`](../docs/kd-pe-105-confidence-model.md)
  — факторы, правило минимума, таблица решений, контрольные случаи;
- [`plans/kd-pe-105-block4-report.md`](../plans/kd-pe-105-block4-report.md) —
  этот отчёт.

Изменены:

- [`e3_pe3_inventory.py`](../e3_pe3_inventory.py):
  - `compare_inventories()` больше не вычисляет `confidence` и `warning` в
    нескольких местах, а вызывает `assess_finding()`;
  - `_match_confidence()` сохранён как совместимая обёртка;
  - `valid_details()` использует исполняемую таблицу решений
    `validate_combination()` и отклоняет противоречивые комбинации;
  - в `message` добавлено основание достоверности с ограничивающим фактором.
- [`docs/e3-pe3-details-schema.md`](../docs/e3-pe3-details-schema.md) —
  добавлен раздел о модели; схема v1 не расширялась.

## 3. Единая модель

Модуль использует только дискретные уровни `low` < `medium` < `high` и правило
минимума по обязательным компонентам. Числовые коэффициенты, средние и
эвристические веса не применяются.

| Компонент | Смысл |
|---|---|
| `e3_extraction` | качество извлечения обозначения из Э3 |
| `identifier_classification` | уверенность, что обозначение — самостоятельное изделие |
| `pe3_extraction` | качество извлечения выбранной строки ПЭ3 |
| `row_match` | точность привязки к строке |
| `extraction_completeness` | полный или частичный анализ |
| `candidate_ambiguity` | единственность и неоднозначность кандидатов |
| `reason_constraint` | потолок, заданный таблицей решений |

Правило:

```text
confidence = min(level of mandatory components)
```

Следствия:

- два `medium` не дают `high`;
- `high` возможно только при `high` всех обязательных компонентов;
- `partial`/`truncated` всегда дают `low`;
- необязательные компоненты не меняют итог;
- `no_matching_pe3_row` всегда не выше `medium`, независимо от количества
  одинаковых обозначений.

## 4. Таблица решений

| `reason` | `status` | Максимальный `confidence` | Допустимый `severity` | Строка ПЭ3 |
|---|---|---|---|---|
| `quantity_mismatch` | `evaluated` | `high` только при всех обязательных `high` | `warning` при `high`/`medium`, `info` при `low` | заполнена |
| `row_matched_without_coverage` | `evaluated` | `medium` | только `info` | заполнена |
| `row_ambiguous` | `evaluated` | `low` | только `info` | `null` |
| `no_matching_pe3_row` | `evaluated` | `medium` | только `info` | `null` |
| `reference_only` | `not_evaluated` | `low` | только `info` | `null` |
| `no_text_layer` | `not_evaluated` | `low` | только `info` | `null` |
| `pe3_not_parsed` | `not_evaluated` | `low` | только `info` | `null` |
| `truncated` | `not_evaluated` | `low` | только `info` | `null` |
| `ambiguous_row` | — | запрещено | запрещено | — |

Дополнительно `warning` требует `evidence=under_expanded`, `quantity > entries`,
заполненной строки, отсутствия `partial`/`truncated`, непустого семейства не из
`TERMINAL_FAMILIES`, `confidence` `high`/`medium` и отсутствия
`ambiguous`-компонентов.

## 5. Предотвращённые ложные предупреждения

| Сценарий | Что было бы неверно | Текущее поведение | Тест |
|---|---|---|---|
| KM: одна строка `3KM1`, отсутствует `3KM2` | `warning` + `high` при `ambiguous_row` | `info`, `medium`, `row_matched_without_coverage` | `test_ambiguous_km_row_never_gets_high_without_independent_proof` |
| Контакт `1KM1:A1` без устройства | вывод выдавался за отсутствующее изделие | `not_evaluated`, `reference_only`, `missing=[]` | `test_contact_without_confirmed_device_is_not_a_standalone_error` |
| ПЭ3-строка с `unknown` | нераспознанная строка считалась покрытием | `no_matching_pe3_row`, строка не выбирается | `test_unrecognized_pe3_row_is_not_reported_as_a_match` |
| ПЭ3 не разобран | пустой результат выглядел как успешное сопоставление | `not_evaluated`, `pe3_not_parsed` | `test_unparsed_pe3_is_not_evaluated_instead_of_silently_empty` |
| Частичный/усечённый анализ | категоричный `warning` при неполных данных | `info`, `low`, `partial=true` | `test_partially_processed_document_does_not_create_a_categorical_conclusion` |
| 100 отсутствующих обозначений без строки | количество воспринималось как сила доказательства | `medium`, не `high` | `test_many_identical_designations_do_not_raise_no_matching_confidence` |
| Слабое извлечение Э3 (`split_label`) | `high` игнорировал качество извлечения | `medium`, ограничитель — `e3_extraction` | `test_weakest_mandatory_evidence_caps_confidence` |
| `X1` как неоднозначное семейство | `high` при неоднозначной классификации | `medium`, не выше | `test_ambiguous_identifier_caps_classification_at_medium` |
| Старое противоречие `ambiguous_row` + `high` | противоречие проходило валидацию | отклоняется таблицей решений | `test_legacy_contradiction_is_rejected_instead_of_reinterpreted` |

Отдельно проверено, что подтверждённый дефект TA по-прежнему допускает `high`:
`test_confirmed_ta_defect_can_be_high`. Это положительный контроль: модель не
понижает доказанный дефект.

## 6. Тесты

Целевой прогон:

```text
.venv/bin/python -m pytest -q tests/test_kd_pe_105_confidence_model.py
41 passed in 0.44s
```

Полный прогон:

```text
.venv/bin/python -m pytest -q
1063 passed, 1 skipped, 1 warning in 101.90s (0:01:41)
```

- База блока 3: `1022 passed, 1 skipped`.
- Добавлено 41 тест блока 4 → `1063 passed`.
- Skip: `tests/test_parser_process.py:89` — Windows Job Object bootstrap only.
- Warning: Starlette/httpx deprecation, не связан с изменениями.
- Новых необъяснённых ошибок нет.

## 7. Обратная совместимость JSON, Excel и истории

- `details` schema v1 не расширялась: набор ключей и `schema_version=1`
  сохранены. Проверка: `test_details_schema_v1_is_not_extended`.
- CSV остаётся строго 6-колоночным; Excel — 10 колонок без `details` и 12 с
  `details`. Проверка:
  `test_excel_keeps_two_additive_columns_and_history_identity_is_stable`.
- `finding_identity()` возвращает одинаковый fingerprint для одинакового
  замечания; решения инженера и журнал изменений не мигрируются.
- Ранее сохранённые отчёты не переписываются. Валидатор применяется к новым
  результатам парсера; старые записи читаются как есть.
- Легитимный старый отчёт с `quantity_mismatch` + `high` остаётся валидным:
  `test_legacy_v1_report_with_quantity_mismatch_is_still_valid`.
- `KD-PE-100`–`KD-PE-104` не изменялись.

## 8. Критерии приёмки блока 4

- [x] Введена централизованная модель оценки достоверности.
- [x] Достоверность чтения обозначения отделена от достоверности сопоставления
      с ПЭ3.
- [x] Учитываются качество извлечения Э3, качество извлечения ПЭ3, уверенность
      классификации, точность соответствия строки, полнота извлечения и
      неоднозначность кандидатов.
- [x] Итоговая достоверность не превышает ограничение самого слабого
      обязательного доказательства.
- [x] При `no_matching_pe3_row` количество одинаковых обозначений не повышает
      достоверность.
- [x] Неполный анализ явно отражается через `partial`/`not_evaluated` и не даёт
      категоричного вывода.
- [x] Противоречия между `reason`, `confidence` и уровнем замечания
      отклоняются.
- [x] Произвольные числовые коэффициенты не используются.
- [x] Формат `details` schema v1 сохранён.
- [x] Смысл ранее сохранённых отчётов и история инженерных решений не
      изменяются.
- [x] Добавлены позитивные и негативные тесты, включая обязательные
      контрольные случаи.
- [x] Полный pytest: `1063 passed, 1 skipped`.
- [x] Подготовлен отчёт о предотвращённых ложных предупреждениях.

## 9. Ограничения

1. Реальные PDF `ТНБВ.656451.162.Э3`/`ПЭ3` и XLSX-отчёт недоступны в
   окружении. Приёмка на реальных документах остаётся ручным шагом заказчика.
2. Модель оценивает качество текстового сопоставления, а не физическое наличие
   элемента.
3. Классификация и извлечение остаются эвристическими; модель делает их
   ограничения явными, но не устраняет.
4. Блок 5 не начинался.
