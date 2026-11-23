# KD-PE-107: регрессионные случаи и эталонный синтетический комплект

Дата: 23.11.2026. Все документы синтетические, создаются в памяти (PyMuPDF).
Реальные Э3/ПЭ3 не использовались и не добавлялись.

## 1. Три категории регрессий

Каждое правило проверяется в трёх независимых категориях.

| Категория | Что доказывает | Критерий |
|---|---|---|
| **Подтверждённый дефект** | правило находит реальное несоответствие | ожидаемый код и уровень присутствуют |
| **Отсутствие ложного срабатывания** | правило молчит на корректных данных | ожидаемого кода нет |
| **Неоднозначный случай** | правило не угадывает | `ambiguous`/`info`/`not_evaluated`, без warning |

Основной файл: [`tests/test_kd_pe_107_regression.py`](../tests/test_kd_pe_107_regression.py).

### 1.1. Подтверждённый дефект

| Тест | Вход | Ожидание |
|---|---|---|
| `test_confirmed_conflict_of_two_incompatible_products` | `KLD3` 24VDC + 230VAC | `KD-PE-106` warning, обе строки |
| `test_confirmed_missing_device_is_still_reported` | `QF3` в Э3, нет в ПЭ3 | `KD-PE-105` warning |
| `test_confirmed_quantity_contradiction_is_a_warning` | 7 раскрытых, количество 9 | `KD-PE-105` warning |
| `test_ambiguous_confusable_characters_are_informational` | `SO1.PLC.01` / `S01.PLC.01` | `KD-PE-107` info |

### 1.2. Отсутствие ложного срабатывания

| Тест | Вход | Ожидание |
|---|---|---|
| `test_false_missing_for_grouped_ta_row` | `TA2;TA3`, количество 2 | нет `KD-PE-105` |
| `test_false_duplicate_for_compatible_multisheet_reference` | один QF1 на двух листах | нет `KD-PE-106` warning |
| `test_false_conflict_for_terminal_accessories` | клеммный блок `XT0–XT4` | нет `KD-PE-106` warning |

### 1.3. Неоднозначный случай

| Тест | Вход | Ожидание |
|---|---|---|
| `test_ambiguous_missing_text_layer_is_not_evaluated` | PDF без текстового слоя | `not_evaluated`, без warning |
| `test_ambiguous_line_break_gap_stays_unknown` | `XT1`⏎`2` | `ambiguous`, без догадки |
| `test_ambiguous_bare_x_identifier_requires_context` | одиночное `X1` | `ambiguous`, без warning |

## 2. Эталонный синтетический комплект

Модуль [`kd_pe_107_reference.py`](../kd_pe_107_reference.py) строит пять
контрольных сценариев и прогоняет их через реальный конвейер
`kd_crosscheck.run()` / `check_spec_duplicates()`.

Запуск:

```bash
python tools/kd_pe_107_selfcheck.py
python tools/kd_pe_107_selfcheck.py --json kd-pe-107-selfcheck.json
```

Код выхода `0` — все сценарии PASS, `1` — есть FAIL.

### 2.1. Сценарии

| Сценарий | Вход | Ожидается | Критерий |
|---|---|---|---|
| `group_row` | Э3: `TA2`,`TA3`; ПЭ3: `TA2\nTA3`, кол. 2 | нет missing, совпадений 2 | `missing=[]`, `совпадений — 2`, нет `KD-PE-106` |
| `conflict` | ПЭ3: `KLD1...KLD3` (24VDC) + `KLD3` (230VAC) | `KD-PE-106` warning | обе строки, `24VDC`, `230VAC` |
| `confusable` | Э3: `SO1.PLC.01`; ПЭ3: `S01.PLC.01` | `KD-PE-107` info | пара `O/0`, оба значения в тексте |
| `families` | Э3: `K1`,`KA1`,`KL1`,`HL1`,`KLD1`; ПЭ3: `XT1` | все пять в missing | `labels ⊆ missing` |
| `markers` | Э3: `K1 (лист 3)`, `K2:1 - XT1:2` | `K1` проверяется, `K2` — ссылка | `K1 ∈ missing`, `K2 ∉ missing` |

### 2.2. Таблица эффективности

Актуальный результат прогона (`5/5 PASS`, exit 0):

| Сценарий | Ожидается | Фактически | Результат |
|---|---|---|---|
| `group_row` | PASS | `KD-PE-105 missing=none; overlap2=True` | PASS |
| `conflict` | PASS | `KD-PE-106=warning; rows=2` | PASS |
| `confusable` | PASS | `KD-PE-107=info; pair=O/0` | PASS |
| `families` | PASS | `KD-PE-105 missing=[HL1, K1, KA1, KLD1, KL1]` | PASS |
| `markers` | PASS | `KD-PE-105 missing=[K1]; K2_is_reference=True` | PASS |

Значения `actual` в таблице формируются самим прогоном; при изменении кода
таблица обновляется автоматически через `render_table()`.

## 3. Структура ПЭ3 (блок H)

Файл: [`tests/test_kd_pe_107_pe3_structure.py`](../tests/test_kd_pe_107_pe3_structure.py).

| Тест | Проверяет |
|---|---|
| `test_multiline_cell_and_semicolon_group_are_one_row` | многострочная ячейка и `;`-группа — одна строка |
| `test_empty_designator_accessory_row_is_not_an_error` | пустое обозначение комплектующего не даёт замечания |
| `test_terminal_block_with_mixed_terminals_is_not_a_conflict` | `XT0–XT4` — не конфликт |
| `test_compatible_multisheet_repeat_is_informational_not_warning` | повтор на двух листах — info |
| `test_merged_rect_cells_are_parsed_without_false_quantity_warning` | объединённые `re`-ячейки не дают ложного количества |
| `test_range_quantity_mismatch_remains_visible` | реальное несоответствие количества видно |

## 4. Сопоставление и маркеры

Файл: [`tests/test_kd_pe_107_matching.py`](../tests/test_kd_pe_107_matching.py).

| Тест | Проверяет |
|---|---|
| `test_universal_group_separators_expand` | `;`, `,`, диапазоны, тире, перенос |
| `test_ambiguous_gaps_are_not_invented` | `TA2 2`, `TA2\n2`, `1KM1...1KM3\n4` — ambiguous |
| `test_multiline_group_row_is_parsed_and_matched` | многострочная группа сопоставляется |
| `test_group_row_variants_do_not_create_missing_findings` | 6 разделителей не дают missing |
| `test_quantity_not_matching_expanded_group_stays_a_warning` | реальное несоответствие количества — warning |
| `test_pe3_text_evidence_prevents_a_false_missing_product` | текст ПЭ3 снимает ложный missing |
| `test_different_families_in_one_group_row_are_not_merged` | разные семейства не сливаются |

## 5. Конфликт повторного обозначения

Файл: [`tests/test_designation_conflicts.py`](../tests/test_designation_conflicts.py).

| Тест | Проверяет |
|---|---|
| `test_name_comparison_is_attribute_based` | сравнение по атрибутам, не по строке |
| `test_voltage_conflict_reports_both_rows_without_choosing_a_winner` | обе строки, без выбора |
| `test_type_conflict_is_universal_for_other_families` | тип-конфликт для других семейств |
| `test_compatible_repeat_is_not_a_conflict` | совместимый повтор — не конфликт |
| `test_ambiguous_repeat_is_informational` | неоднозначный повтор — info |
| `test_terminal_assembly_repeats_are_not_conflict_warnings` | клеммники — без warning |
| `test_range_and_singleton_intersection_is_detected` | диапазон + одиночное обозначение |
| `test_two_ranges_intersection_is_detected` | два диапазона |
| `test_rows_without_grid_bands_are_not_conflict_evidence` | нет сетки — нет доказательства |
| `test_header_area_is_not_a_product_row` | заголовок — не строка изделия |

## 6. Похожие символы

Файл: [`tests/test_confusable_designations.py`](../tests/test_confusable_designations.py).

| Тест | Проверяет |
|---|---|
| `test_confusable_pairs_are_detected` | `O/0`, `I/1`, `S/5`, `B/8` |
| `test_true_matches_and_unrelated_values_are_not_candidates` | равные и несвязанные — не кандидаты |
| `test_document_codes_and_numeric_versions_are_not_model_tokens` | документные коды исключены |
| `test_both_spellings_on_both_sides_are_not_a_mismatch` | оба написания с обеих сторон |
| `test_candidate_is_informational_and_preserves_source_values` | info, исходные значения сохранены |
| `test_no_candidate_without_a_model_token_in_pe3` | нет модельного токена — нет кандидата |

## 7. Усечение контекста

Файл: [`tests/test_kd_pe_107_context_truncation.py`](../tests/test_kd_pe_107_context_truncation.py).

| Тест | Проверяет |
|---|---|
| `test_long_reference_context_is_marked_and_bounded` | маркер `…`, длина ≤ лимита |
| `test_truncated_context_remains_schema_valid` | усечённый контекст валиден |
| `test_pe3_name_never_exceeds_the_schema_limit` | имя ПЭ3 ≤ 300 |

## 8. Отчёты

Файл: [`tests/test_kd_pe_107_reporting.py`](../tests/test_kd_pe_107_reporting.py).

| Тест | Проверяет |
|---|---|
| `test_parser_worker_accepts_plain_new_codes` | `KD-PE-106/107` без `details` |
| `test_new_codes_are_registered_in_the_rule_catalog` | коды в `quality/rule_catalog.py` |
| `test_excel_report_contains_every_required_engineering_field` | обязательные поля Excel |

## 9. Сводка покрытия

| Блок KD-PE-107 | Файлы тестов |
|---|---|
| A. Группы и колонка | `test_kd_pe_107_matching.py`, `test_kd_pe_107_pe3_structure.py` |
| B/D. Семейства и конфликт | `test_designation_conflicts.py`, `test_kd_pe_106_acceptance.py` |
| C. Похожие символы | `test_confusable_designations.py` |
| E. Маркеры | `test_kd_pe_107_matching.py`, `test_kd_pe_107_regression.py` |
| F. Windows | `test_windows_acceptance_script.py` |
| G. Усечение | `test_kd_pe_107_context_truncation.py` |
| H. Структура ПЭ3 | `test_kd_pe_107_pe3_structure.py` |
| I/J. Три категории и эталон | `test_kd_pe_107_regression.py` |
| K. Отчёты | `test_kd_pe_107_reporting.py` |

Итоговый прогон: **1187 passed, 2 skipped, 1 warning**. Два skip —
платформенные (Windows Job Object, PowerShell), не относятся к логике
KD-PE-107.
