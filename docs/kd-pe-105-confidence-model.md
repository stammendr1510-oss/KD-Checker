# KD-PE-105: единая модель достоверности и severity (блок 4)

Дата: 21.11.2026. Продолжение блоков 1–3
([`kd-pe-105-diagnostics.md`](kd-pe-105-diagnostics.md),
[`kd-pe-105-matching-evidence.md`](kd-pe-105-matching-evidence.md),
[`kd-pe-105-contextual-classification.md`](kd-pe-105-contextual-classification.md)).

Модуль: [`kd_pe_105_confidence.py`](../kd_pe_105_confidence.py).
Интеграция: [`e3_pe3_inventory.py`](../e3_pe3_inventory.py).
Тесты: [`tests/test_kd_pe_105_confidence_model.py`](../tests/test_kd_pe_105_confidence_model.py).

## 1. Задача блока 4

Блоки 1–3 исправили конкретные противоречия, но `confidence` и `severity`
оставались вычисляемыми в нескольких местах. Блок 4 вводит **одну точку
принятия решения**: все факторы достоверности собираются в явные компоненты,
итоговая достоверность не может превысить самое слабое обязательное
доказательство, а `reason`, `confidence` и уровень замечания проверяются на
совместимость.

Ключевое требование: каждое `high` должно иметь объяснимую доказательную
основу. Неоднозначная строка KM не может получить `high` только потому, что
она хорошо извлечена.

## 2. Факторы достоверности

Модель не использует числовые коэффициенты и не усредняет оценки. Каждый
фактор имеет дискретный уровень `low` < `medium` < `high`.

| Компонент | Что измеряет | `high` | `medium` | `low` |
|---|---|---|---|---|
| `e3_extraction` | качество извлечения обозначения из Э3 | `label` | `split_label` или `reference` на 2+ листах | слабое/одиночное упоминание |
| `identifier_classification` | уверенность, что обозначение — самостоятельное изделие | `device_candidate` с высокой уверенностью | `ambiguous` (потолок) | иная низкая уверенность |
| `pe3_extraction` | качество извлечения выбранной строки ПЭ3 | сетка, обозначения и количество извлечены | нет полосы строки | `unknown`, нет обозначений/количества, одно обозначение при количестве > 1 |
| `row_match` | точность привязки к строке | `under_expanded` | `family_group`, `no_matching_pe3_row` (включая `family_only`) | `row_ambiguous`, `family_only` без `no_matching_pe3_row` |
| `extraction_completeness` | полнота анализа | нет `partial`/`truncated` | — | `partial` или `truncated` |
| `candidate_ambiguity` | единственность и неоднозначность строк | уникальная подтверждённая строка | уникальная предположительная строка или отсутствие строки | несколько кандидатов |
| `reason_constraint` | потолок, заданный таблицей решений | `quantity_mismatch` | `row_matched_without_coverage`, `no_matching_pe3_row` | `row_ambiguous`, `not_evaluated`-причины |

`pe3_extraction` не является обязательным, когда строка не выбрана: в этом
случае ограничение задают `row_match`, `candidate_ambiguity` и
`reason_constraint`.

## 3. Правило агрегации

[`aggregate_confidence()`](../kd_pe_105_confidence.py) возвращает минимум по
обязательным компонентам:

```text
confidence = min(level of mandatory components)
```

Следствия:

1. Два `medium` никогда не дают `high`.
2. `high` возможно только при `high` **всех** обязательных компонентов.
3. `partial`/`truncated` всегда дают `low`, потому что
   `extraction_completeness` обязателен.
4. Необязательные компоненты сохраняются для диагностики, но не повышают и не
   понижают итог.
5. Числовые веса, средние и эвристические коэффициенты не используются.

## 4. Таблица решений

[`validate_combination()`](../kd_pe_105_confidence.py) — исполняемая таблица.
Она же вызывается из [`valid_details()`](../e3_pe3_inventory.py).

| `reason` | `status` | Максимальный `confidence` | Допустимый `severity` | `pe3_page` / `pe3_table_row` |
|---|---|---|---|---|
| `quantity_mismatch` | `evaluated` | `high` только при всех обязательных `high`; иначе `medium`/`low` | `warning` при `high`/`medium`; `info` при `low` | заполнены |
| `row_matched_without_coverage` | `evaluated` | `medium` | только `info` | заполнены |
| `row_ambiguous` | `evaluated` | `low` | только `info` | `null` |
| `no_matching_pe3_row` | `evaluated` | `medium`; количество обозначений не повышает | только `info` | `null` |
| `reference_only` | `not_evaluated` | `low` | только `info` | `null` |
| `no_text_layer` | `not_evaluated` | `low` | только `info` | `null` |
| `pe3_not_parsed` | `not_evaluated` | `low` | только `info` | `null` |
| `truncated` | `not_evaluated` | `low` | только `info` | `null` |
| `ambiguous_row` | — | запрещено | запрещено | — |

Дополнительные инварианты:

- `warning` возможен только при `reason=quantity_mismatch`, `evidence=under_expanded`,
  заполненной строке, `quantity > entries`, отсутствии `partial`/`truncated`,
  непустом семействе не из `TERMINAL_FAMILIES`, `confidence` `high`/`medium` и
  отсутствии `ambiguous`-компонентов;
- `high` невозможен при `row_matched_without_coverage`, `row_ambiguous`,
  `no_matching_pe3_row`, `partial` или `truncated`;
- `row_ambiguous` и `no_matching_pe3_row` не могут иметь заполненные
  `pe3_page`/`pe3_table_row`;
- `not_evaluated` всегда `info` и `low`;
- `quantity_mismatch` всегда имеет заполненный `pe3_page`.

## 5. Контрольные случаи

| Случай | Ожидаемое поведение | Тест |
|---|---|---|
| Однозначный дефект TA: уникальная строка, `quantity=9`, раскрыто 7 | `warning`, `high` | `test_confirmed_ta_defect_can_be_high` |
| Неоднозначная строка KM: одна строка `3KM1`, отсутствует `3KM2` | `info`, `medium`, `row_matched_without_coverage`; `high` отклоняется | `test_ambiguous_km_row_never_gets_high_without_independent_proof` |
| Контакт/вывод `1KM1:A1` без подтверждённого устройства | `not_evaluated`, `reference_only`, `missing=[]` | `test_contact_without_confirmed_device_is_not_a_standalone_error` |
| Нераспознанная строка ПЭ3 (`unknown`) | `no_matching_pe3_row`, строка не считается покрытием | `test_unrecognized_pe3_row_is_not_reported_as_a_match` |
| ПЭ3 вообще не разобран | `not_evaluated`, `pe3_not_parsed` | `test_unparsed_pe3_is_not_evaluated_instead_of_silently_empty` |
| Частично обработанный документ | `info`, `low`, `partial=true`, `warning` отклоняется | `test_partially_processed_document_does_not_create_a_categorical_conclusion` |
| 100 отсутствующих обозначений при `no_matching_pe3_row` | `medium`, не `high` | `test_many_identical_designations_do_not_raise_no_matching_confidence` |
| Слабое извлечение Э3 (`split_label`) при подтверждённой строке | `medium`, ограничитель — `e3_extraction` | `test_weakest_mandatory_evidence_caps_confidence` |
| `X1` как неоднозначное семейство | `medium`, не выше | `test_ambiguous_identifier_caps_classification_at_medium` |

## 6. Что предотвращено

| Ложное/завышенное предупреждение | Прежний риск | Текущее поведение |
|---|---|---|
| KM `reason=ambiguous_row`, `confidence=high`, warning | хорошо извлечённая, но чужая строка выдавалась за подтверждённую ошибку | `info`, `medium`, `row_matched_without_coverage` |
| Контакт/вывод как отсутствующее изделие | вывод устройства создавал отдельное замечание | `reference_only`, `not_evaluated`, без `missing` |
| Нераспознанная строка ПЭ3 как успешное сопоставление | `unknown` не мешал считать покрытие | `no_matching_pe3_row`, строка не выбирается |
| Частичный анализ как категоричный вывод | warning при `partial`/`truncated` | `info`, `low`, `partial=true` |
| Рост достоверности от количества одинаковых обозначений | много отсутствующих обозначений могли восприниматься как сильное доказательство | `no_matching_pe3_row` всегда ≤ `medium` |
| `high` при слабом извлечении Э3 | `high` не учитывал качество извлечения | минимум по обязательным компонентам |

## 7. Совместимость

- Схема `details` v1 **не расширялась полями**. Набор ключей и
  `schema_version=1` сохранены.
- Модель влияет только на значения `confidence`, `severity`, `reason`,
  `status`, `partial` и `truncated`.
- CSV остаётся строго 6-колоночным; Excel — 10 колонок без `details` и 12 с
  `details`.
- SQLite-схема, история, `finding_identity()` и решения инженера не меняются.
- Ранее сохранённые отчёты не переписываются. Валидатор применяется к новым
  результатам парсера; старые записи читаются как есть.
- Устаревшая причина `ambiguous_row` по-прежнему отклоняется для новых
  результатов, как и в блоке 2.

## 8. Ограничения

- Нет OCR; текстовый слой обязателен.
- Модель оценивает качество текстового сопоставления, а не физическое наличие
  элемента.
- Классификация и извлечение остаются эвристическими; модель делает их
  ограничения явными, но не устраняет.
- Реальные PDF предприятия в репозиторий не добавлялись.
