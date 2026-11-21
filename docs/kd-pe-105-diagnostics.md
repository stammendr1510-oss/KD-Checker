# KD-PE-105: диагностическая база (блок 1)

Дата: 21.11.2026. Ветка: `diagnostics/kd-pe-105-baseline` от
`a968ef8d533281369dfecdc99df6615cba771e24` (`feature/e3-pe3-missing-designations`).

Документ фиксирует **воспроизводимую диагностику** опубликованной реализации KD-PE-105.
Основной алгоритм в этом блоке не изменялся. Все примеры синтетические.

## 1. Доказательная база и её границы

| Источник | Доступность в окружении | Как использован |
|---|---|---|
| `ТНБВ.656451.162.Э3 Щит 6.5-NDB-09.pdf` | **нет** | не открывался; содержимое не проверялось |
| `ТНБВ.656451.162.ПЭ3 Щит 6.5-NDB-09.pdf` | **нет** | не открывался; содержимое не проверялось |
| `kd-report-1 (1)(1).xlsx` | **нет** | не открывался; 11 замечаний взяты из постановки как контрольные факты |
| Исходный код `a968ef8` | да | прочитан, ветви прослежены |
| Синтетические PDF/строки | да | новые тесты `tests/test_kd_pe_105_diagnostics.py` |

Следствия:

1. Утверждения о **содержимом** реальных Э3/ПЭ3 в этом документе не являются независимо
   проверенными. Они помечены как «факт постановки».
2. Номера строк таблицы ПЭ3, точные `entries_count` и порядок замечаний в реальном XLSX
   не восстановлены. Для них указано «неизвестно».
3. Восстановление состава 11 замечаний (3 warning + 8 info) — **гипотеза по исключению**,
   а не чтение отчёта. Она проверяема только на исходном XLSX.
4. Заявленные в постановке `950 passed, 5 skipped` на Windows 11 в этом окружении не
   воспроизводимы. Независимый Linux-прогон на `a968ef8` даёт `954 passed, 1 skipped`;
   после добавления 20 диагностических тестов — `974 passed, 1 skipped`. Расхождение
   954 ↔ 950 не объяснено и требует Windows-логов.

## 2. Карта конвейера

```
kd_crosscheck.run()                                    kd_crosscheck.py:390
├─ get_spec_rows(pe_bytes)                             kd_crosscheck.py:198
│  ├─ _quantity_words / _row_band / _cell_words        kd_crosscheck.py:84,118,132
│  ├─ expand_notation / _notation_part                 kd_crosscheck.py:46,34
│  └─ duplicate_eligible                               kd_crosscheck.py:254
├─ scheme_designations + extract_mentions              kd_crosscheck.py:310, e3_pe3_inventory.py:201
├─ KD-PE-102 (quantity vs entries)                     kd_crosscheck.py:446
├─ KD-PE-100 / KD-PE-101                               kd_crosscheck.py:474,477
└─ compare_inventories(...)                            e3_pe3_inventory.py:517
   ├─ build_components                                 e3_pe3_inventory.py:263
   ├─ missing / ambiguous / matched                    e3_pe3_inventory.py:545
   ├─ _attribute_row / _row_score / _row_confidence    e3_pe3_inventory.py:332,317,296
   ├─ warning predicate + reason                       e3_pe3_inventory.py:589,600
   └─ _details_for_group (confidence)                  e3_pe3_inventory.py:456
```

`parser_process._validate_result` принимает `details` только для `KD-PE-105` и вызывает
[`valid_details()`](e3_pe3_inventory.py:741). Валидатор проверяет схему, но **не проверяет
согласованность `reason` и `confidence`**; поэтому спорная комбинация проходит как валидная.

## 3. Происхождение полей

| Поле | Где формируется | От чего зависит | Диагностический риск |
|---|---|---|---|
| `confidence` | [`_details_for_group()`](e3_pe3_inventory.py:456), строка 460 | `severity == "warning"` → `high`, иначе `medium` | не является мерой качества сопоставления; high = «это warning» |
| `reason` | [`compare_inventories()`](e3_pe3_inventory.py:600) | `row is None` → `no_matching_pe3_row`; `quantity > entries` → `quantity_mismatch`; иначе → `ambiguous_row` | `ambiguous_row` — catch-all, а не признак неоднозначности |
| `missing` | [`compare_inventories()`](e3_pe3_inventory.py:545) | обозначение не найдено в `pe3_entries`/`base_of`, а компонент не `low` | контактные суффиксы схлопываются к базе |
| `pe3_page` | [`_details_for_group()`](e3_pe3_inventory.py:474) | `row["page"]` | при `row=None` отсутствует |
| `pe3_table_row` | [`_details_for_group()`](e3_pe3_inventory.py:475) | `row["table_row"]`, только если `row_band is not None` | строка скрывается без сетки |
| `pe3_quantity` | [`_details_for_group()`](e3_pe3_inventory.py:476) | `row["quantity"]` | не сверяется с покрытием `missing` |
| `pe3_entries_count` | [`_details_for_group()`](e3_pe3_inventory.py:477) | `len(row["entries"])` | только для выбранной строки |
| `duplicate_eligible` | [`get_spec_rows()`](kd_crosscheck.py:254) | сетка + containment + complete + точное написание диапазона | единственный путь к `_row_confidence == "high"` при `quantity == entries` |

## 4. Диагностическая таблица 11 замечаний

Порядок и номера — реконструкция. В реальном XLSX порядок может отличаться.
`н/д` — поле в постановке не приведено; `неизв.` — без XLSX не устанавливается.

| № | Код | Severity | Объект | `reason` | `confidence` | `pe3_table_row` | `pe3_quantity` / entries | Ветвь алгоритма | Доказательство |
|---|---|---|---|---|---|---|---|---|---|
| 1 | KD-PE-102 | warning | TA, диапазон ПЭ3 | — | — | неизв. | 9 / 7 | [`run()`](kd_crosscheck.py:446): `len(entries)>1 and len(entries)!=quantity` | подтверждено кодом и тестом `test_control_reports_exactly_3ta2_and_3ta3` |
| 2 | KD-PE-105 | warning | TA: `3TA2`, `3TA3` | `quantity_mismatch` | `high` | неизв. | 9 / 7 | [`compare_inventories()`](e3_pe3_inventory.py:581), warning при `quantity_mismatch` | подтверждено синтетическим тестом |
| 3 | KD-PE-105 | info | HLG | `no_matching_pe3_row` | `medium` | `null` | `null` | [`_attribute_row()`](e3_pe3_inventory.py:332) вернул `None` | подтверждено синтетическим тестом |
| 4 | KD-PE-105 | info | KK | `no_matching_pe3_row` | `medium` | `null` | `null` | то же | подтверждено синтетическим тестом |
| 5 | KD-PE-105 | info | QF | `no_matching_pe3_row` | `medium` | `null` | `null` | то же | подтверждено синтетическим тестом |
| 6 | KD-PE-105 | info | SB | `no_matching_pe3_row` | `medium` | `null` | `null` | то же | подтверждено синтетическим тестом |
| 7 | KD-PE-105 | warning | KM | `ambiguous_row` | `high` | неизв. | 1 / неизв. | warning по `_row_confidence == high`; reason — catch-all | воспроизведено точной комбинацией; см. §5 |
| 8 | KD-PE-105 | info | SF | неизв. | `medium` или `low` | неизв. | неизв. | зависит от класса упоминания; см. §7 | **гипотеза**, требуется XLSX |
| 9 | KD-PE-105 | info | X | неизв. | `medium` или `low` | неизв. | неизв. | `TERMINAL_FAMILIES` запрещает warning | частично подтверждено синтетикой |
| 10 | KD-PE-105 | info | XT | неизв. | `medium` или `low` | неизв. | неизв. | `TERMINAL_FAMILIES` запрещает warning | частично подтверждено синтетикой |
| 11 | KD-PE-100 | info | сводка сопоставления | — | — | — | — | [`run()`](kd_crosscheck.py:474) добавляет всегда | по исключению: иначе info было бы 9 |

Проверка счёта: 3 warning = №1, №2, №7. 8 info = №3–№6 (4) + №8–№10 (3) + №11 (1).
Если бы присутствовал KD-PE-101, info было бы 9; при заявленных 11 замечаниях его нет.

## 5. Почему KM получил `confidence=high` при `reason=ambiguous_row` и количестве 1

Это не случайная порча данных, а следствие трёх независимых решений.

### 5.1 `confidence` выводится из severity, а не из строки

[`_details_for_group()`](e3_pe3_inventory.py:460):

```python
result["confidence"] = "high" if severity == "warning" else "medium"
```

Значит `high` означает «замечание является warning», а не «строка ПЭ3 сопоставлена
однозначно». Компонент Э3 с `confidence=high` при info-замечании всё равно получит
`details.confidence=medium` (проверено `test_component_label_confidence_does_not_reach_details_confidence`).

### 5.2 Warning для KM требует `_row_confidence == "high"`

[`_row_confidence()`](e3_pe3_inventory.py:296) возвращает `high` только при
`duplicate_eligible` (строки 301–302). Для строки «количество 1 / одно обозначение»
это единственный путь: `row_band` даёт максимум `medium`, а `quantity > len(entries)` ложно.

[`duplicate_eligible`](kd_crosscheck.py:254) требует одновременно:

- `row_band` существует (сетка таблицы);
- все слова обозначений целиком внутри узкой вертикальной ячейки (`contained`);
- в ячейке нет пропущенных слов (`complete`);
- нет `unknown`;
- диапазоны записаны без ведущих нулей.

То есть `high` у KM — это косвенное доказательство, что строка была **полностью
ограничена сеткой и полностью распознана**, а не доказательство её смыслового соответствия.

### 5.3 `ambiguous_row` — не «неоднозначная строка», а «строка найдена, количества хватает»

[`compare_inventories()`](e3_pe3_inventory.py:600):

```python
if row is None:
    reason = "no_matching_pe3_row"
elif quantity_mismatch:
    reason = "quantity_mismatch"
else:
    reason = "ambiguous_row"
```

При `quantity == 1` и `len(entries) == 1` `quantity_mismatch` ложно
(проверяется только `quantity > len(entries)`, строка 581–584), поэтому выбирается
`ambiguous_row`. Фактически [`_attribute_row()`](e3_pe3_inventory.py:332) уже вернул
**единственного** победителя; никакой неоднозначности на этом шаге нет.

### 5.4 Проверяемое предсказание

Если в реальном XLSX у замечания №7 одновременно:

- `pe3_quantity = 1`,
- `reason = ambiguous_row`,
- `confidence = high`,

то реализация `a968ef8` **обязана** иметь `duplicate_eligible = true` у выбранной строки,
а значит `pe3_table_row` и `pe3_row_band_pt` не `null`. Если в отчёте `pe3_table_row = null`,
гипотеза неверна и требуется отдельный разбор источника `high`.

Синтетическое воспроизведение: `test_km_quantity_one_with_duplicate_evidence_is_ambiguous_row_and_high`.
Контроль: без `duplicate_eligible` тот же сценарий даёт `info` + `confidence=medium`
(`test_km_quantity_one_without_duplicate_evidence_is_info_medium`).

## 6. Обозначение изделия против обозначения контакта

| Механизм | Код | Поведение | Риск |
|---|---|---|---|
| `TAG` | [`kd_crosscheck.py:15`](kd_crosscheck.py:15) | `(?:[123])?(QF|SF|...|XT|X|PAV)\d+(?:\.\d+)?`, границы `(?<![\w.])` и `(?=\s*:\|\b)` | `XT1.1` распознаётся целиком; `13TA2` не распознаётся |
| `base_of` | [`e3_pe3_inventory.py:153`](e3_pe3_inventory.py:153) | срезает `.N`, если префикс сам является полным `TAG` | `XT1.1` → `XT1`; контакт считается присутствующим при наличии базы |
| `_classify_mention` | [`e3_pe3_inventory.py:168`](e3_pe3_inventory.py:168) | `после `:` → reference`; строка с `:`/соединением → reference; одиночное слово → label | ссылка `SF1:1 - XT1:2` не становится label |
| `TERMINAL_FAMILIES` | [`e3_pe3_inventory.py:41`](e3_pe3_inventory.py:41) | `X, XT, XN, XL, XG` никогда не дают warning | контактные/клеммные расхождения остаются info |
| `_suggest_range` | [`e3_pe3_inventory.py:343`](e3_pe3_inventory.py:343) | строит диапазон только при непрерывной нумерации одного префикса | не доказывает принадлежность строки |

Ключевой вывод: контакт **не является отдельным изделием** для KD-PE-105. Если в Э3
есть `XT1.1`, а в ПЭ3 есть `XT1`, замечания не будет. Это может быть как корректным
(клеммник один), так и ложноположительным/ложноотрицательным результатом, если контакт
обозначает отдельную позицию.

## 7. Диапазоны, переносы, группировка и количество в ПЭ3

1. [`_notation_part()`](kd_crosscheck.py:34) склеивает фрагменты без пробела, но
   прекращает склейку, если уже собрано полное обозначение/диапазон и новый фрагмент
   не начинается с точки. Это защищает от `XT1` + `2` → `XT12`.
2. [`expand_notation()`](kd_crosscheck.py:46) делит запись по `;`, поддерживает `..`/`...`,
   необязательный правый префикс и предел 100 номеров. Больше 100 → `unknown`.
3. Ведущие нули (`1KM01...1KM03`) раскрываются через `int`, поэтому
   [`_duplicate_range_spelling_is_exact()`](kd_crosscheck.py:153) запрещает использовать
   такую строку как доказательство дубля. На KD-PE-105 это не влияет напрямую, но
   `duplicate_eligible=false` понижает `confidence` до `medium`.
4. Количество **не порождает** обозначения. `3TA2`/`3TA3` появляются только из текста Э3,
   а не из `quantity=9`. Поэтому KD-PE-102 (9 против 7) и KD-PE-105 (3TA2/3TA3) — разные,
   но связанные симптомы одной неполной записи ПЭ3.
5. [`_row_band()`](kd_crosscheck.py:118) принимает полосу только если внутри неё ровно
   одна привязка количества. [`_cell_words()`](kd_crosscheck.py:132) без сетки расширяет
   ячейку только в пределах PDF-блока, пересекающего данное количество.
6. [`_row_score()`](e3_pe3_inventory.py:317) сравнивает только семейство, ведущую группу,
   штраф за `quantity > entries`, страницу и номер строки. Он **не проверяет**, входит ли
   отсутствующее обозначение в диапазон выбранной строки.
7. [`_attribute_row()`](e3_pe3_inventory.py:332) при равенстве лучших оценок возвращает
   `None`; это превращается в `no_matching_pe3_row`, даже если строки физически есть.

## 8. Причины возможных ложных замечаний

| № | Причина | Где | Тип ошибки |
|---|---|---|---|
| 1 | `confidence` = функция severity | [`_details_for_group()`](e3_pe3_inventory.py:460) | завышенная уверенность |
| 2 | `ambiguous_row` — catch-all | [`compare_inventories()`](e3_pe3_inventory.py:600) | неверная семантика `reason` |
| 3 | `duplicate_eligible` → `high` без проверки покрытия `missing` | [`_row_confidence()`](e3_pe3_inventory.py:296) | warning на «полной», но чужой строке |
| 4 | Строка выбирается по семейству/группе, а не по диапазону | [`_attribute_row()`](e3_pe3_inventory.py:332) | ложное сопоставление |
| 5 | `quantity < len(entries)` не считается mismatch | [`compare_inventories()`](e3_pe3_inventory.py:581) | пропуск аномалии |
| 6 | Контактный суффикс схлопывается к базе | [`base_of()`](e3_pe3_inventory.py:153) | ложное «присутствует» |
| 7 | `TERMINAL_FAMILIES` принудительно info | [`e3_pe3_inventory.py:41`](e3_pe3_inventory.py:41) | ложное «не warning» |
| 8 | Reference-only без строки семейства молчит | [`compare_inventories()`](e3_pe3_inventory.py:555) | ложное отсутствие замечания |
| 9 | `scheme_designations` склеивает две соседние строки в обозначение | [`kd_crosscheck.py:310`](kd_crosscheck.py:310) | ложное обозначение в Э3 |
| 10 | Эвристика `_classify_mention` зависит от строки/двоеточий | [`e3_pe3_inventory.py:168`](e3_pe3_inventory.py:168) | неверный `kind`/`confidence` |
| 11 | Нормализация кириллицы/латиницы объединяет разные знаки | [`normalized()`](kd_crosscheck.py:20) | ложное совпадение/расхождение |
| 12 | Геометрия ПЭ3 (столбцы, 83% высоты, заголовок «Кол») | [`get_spec_rows()`](kd_crosscheck.py:198) | пропуск или сдвиг строк |
| 13 | Обрезка/частичность понижает только severity, но не помечает причину | [`compare_inventories()`](e3_pe3_inventory.py:589) | неполный анализ выглядит как полный |
| 14 | `table_row` скрывается без `row_band` | [`_details_for_group()`](e3_pe3_inventory.py:475) | потеря прослеживаемости |

## 9. Синтетические тесты и результаты

Файл: [`tests/test_kd_pe_105_diagnostics.py`](tests/test_kd_pe_105_diagnostics.py).

| Тест | Что фиксирует | Результат |
|---|---|---|
| `test_ta_quantity_mismatch_is_warning_high_with_quantity_reason` | TA 9/7 → warning, `quantity_mismatch`, high | passed |
| `test_km_quantity_one_with_duplicate_evidence_is_ambiguous_row_and_high` | точное воспроизведение №7 + валидность схемы | passed |
| `test_km_quantity_one_without_duplicate_evidence_is_info_medium` | high невозможен без `duplicate_eligible` | passed |
| `test_quantity_below_expanded_entries_is_not_a_quantity_mismatch` | асимметрия `quantity > entries` | passed |
| `test_missing_family_without_pe3_row_is_info_medium` (×4) | HLG/KK/QF/SB → info, medium, `no_matching_pe3_row` | passed |
| `test_component_label_confidence_does_not_reach_details_confidence` | `confidence` не зависит от компонента | passed |
| `test_equal_scored_rows_are_reported_as_no_matching_pe3_row` | tie → `None` → `no_matching_pe3_row` | passed |
| `test_table_row_is_hidden_without_a_pdf_row_band` | `table_row` без полосы → `null` | passed |
| `test_terminal_families_are_information_only_even_with_verified_row` (×2) | X/XT никогда не warning | passed |
| `test_contact_suffix_matches_the_base_device` | `XT1.1`, `3TA2.1` → база | passed |
| `test_contact_reference_is_classified_as_reference_not_label` | `SF1:1 - XT1:2` → reference | passed |
| `test_reference_only_mention_is_silent_without_a_matching_family` | reference-only без строки → нет замечания | passed |
| `test_reference_only_mention_with_matching_family_is_not_evaluated` | reference-only + строка → `not_evaluated` | passed |
| `test_expand_notation_keeps_group_prefix_and_short_range_form` | группы и `..` | passed |
| `test_expand_notation_joins_wrapped_fragments` | перенос строки | passed |
| `test_expand_notation_rejects_oversized_range` | предел 100 | passed |

Команды и фактические результаты:

```text
.venv/bin/python -m pytest -q tests/test_kd_pe_105_diagnostics.py
20 passed in 0.18s

.venv/bin/python -m pytest -q
974 passed, 1 skipped, 1 warning in 97.09s
```

Skip: `tests/test_parser_process.py:89` (Windows Job Object). Warning: Starlette/httpx.
Windows 11 (`950 passed, 5 skipped`) в этом окружении не проверялся.

## 10. Матрица ожидаемого поведения

«Текущее» подтверждено кодом и/или синтетикой. «Ожидаемое» — **гипотеза/цель** для
блоков 2–4, если не указано иное. Гипотезы намеренно не закреплены тестами.

| Семейство | Текущее поведение | Ожидаемое поведение (гипотеза) | Обоснование | Покрытие |
|---|---|---|---|---|
| **KM** | warning + high + `ambiguous_row`, если строка `duplicate_eligible`, а `quantity == entries` | warning только при доказанном покрытии отсутствующих обозначений строкой; иначе info + `reason=row_matched_without_coverage`; `confidence` — по доказательствам, не по severity | количество 1 не доказывает, что `3KM…` относится к этой строке | `test_km_*` |
| **TA** | warning + high + `quantity_mismatch` при 9 > 7 | сохранить warning при `quantity > entries` и подтверждённой строке; отдельно сообщать `quantity < entries` | положительный результат №1/#2 ожидаем | `test_ta_*`, существующий control |
| **HLG** | info + medium + `no_matching_pe3_row` | info; не называть запись «отсутствующей в перечне», если строка семейства не найдена; `status` можно оставить `evaluated`, но `confidence` — low/medium по доказательствам | нет строки — нет доказательства принадлежности | `test_missing_family_*` |
| **KK** | то же | то же | то же | то же |
| **QF** | то же | то же | то же | то же |
| **SB** | то же | то же | то же | то же |
| **SF** | зависит от класса: reference-only → `not_evaluated` или молчание; label без строки → `no_matching`; label + `duplicate_eligible` → warning/high/`ambiguous_row` | info, пока не доказано, что `SF` — изделие, а не контакт/ссылка; warning только для standalone label + подтверждённой строки + противоречия количества/диапазона | в постановке SF отнесён к спорным | `test_contact_reference_*`, `test_reference_only_*` |
| **X** | info всегда (`TERMINAL_FAMILIES`) | info всегда; контакты `X1:1` не считать изделием; warning не выдавать | клеммники/соединения — терминальные семейства | `test_terminal_families_*` |
| **XT** | info всегда; `XT1.1` → база `XT1` | info всегда; различать «клеммник отсутствует» и «контакт клеммника»; не warning | то же | `test_terminal_families_*`, `test_contact_suffix_*` |

## 11. Функции, требующие изменения в блоках 2–4

Приоритет 1 — семантика результата:

1. [`_details_for_group()`](e3_pe3_inventory.py:456) — перестать выводить `confidence`
   из `severity`; ввести независимую оценку доказательств.
2. [`compare_inventories()`](e3_pe3_inventory.py:517) — заменить трёхзначный `reason`
   на явные состояния: `row_matched`, `row_ambiguous`, `no_matching_pe3_row`,
   `quantity_mismatch`, `quantity_under_expanded`, `reference_only`; пересмотреть
   warning predicate (строки 589–598).
3. [`_row_confidence()`](e3_pe3_inventory.py:296) — не приравнивать `duplicate_eligible`
   к смысловой достоверности; учитывать покрытие `missing` и `quantity < entries`.
4. [`_attribute_row()`](e3_pe3_inventory.py:332) / [`_row_score()`](e3_pe3_inventory.py:317) —
   проверять вхождение отсутствующего обозначения в диапазон/группу строки; различать
   tie и отсутствие строки.
5. [`REASONS` / `REASON_LABELS`](e3_pe3_inventory.py:63) и
   [`valid_details()`](e3_pe3_inventory.py:741) — новая схема/версия, согласованность
   `reason` ↔ `confidence`, запрет warning при неподтверждённой строке.
6. [`parser_process._validate_result()`](parser_process.py:102) — принять новую версию
   `details` и её инварианты.

Приоритет 2 — распознавание обозначений:

7. [`base_of()`](e3_pe3_inventory.py:153) и [`TAG`](kd_crosscheck.py:15) — явно различать
   изделие и контакт; не схлопывать контакт в базу без контекста.
8. [`_classify_mention()`](e3_pe3_inventory.py:168) — отделить label от reference/contact
   по геометрии и окружению, а не только по `:`.
9. [`scheme_designations()`](kd_crosscheck.py:310) — ограничить склейку двух строк,
   чтобы не создавать несуществующие обозначения.
10. [`extract_mentions()`](e3_pe3_inventory.py:201) — сохранять признак «контакт/ссылка»
    в `Mention` и в `missing/ambiguous`.

Приоритет 3 — извлечение ПЭ3:

11. [`get_spec_rows()`](kd_crosscheck.py:198) / [`_cell_words()`](kd_crosscheck.py:132) /
    [`_row_band()`](kd_crosscheck.py:118) — устойчивость к переносам и нестандартной сетке.
12. [`expand_notation()`](kd_crosscheck.py:46) / [`_notation_part()`](kd_crosscheck.py:34) —
    политика ведущих нулей и предела диапазона.
13. [`run()`](kd_crosscheck.py:446) — условие KD-PE-102 `len(entries)>1` и связь
    с KD-PE-105; не выдавать warning без подтверждённой строки.

## 12. Что не закреплено тестами (открытые гипотезы)

1. Должен ли KM с `quantity == entries` и `duplicate_eligible` быть warning. Текущий тест
   фиксирует поведение, но не объявляет его правильным.
2. Должен ли `status` быть `evaluated` при `no_matching_pe3_row`. Возможен `not_evaluated`.
3. Должен ли SF быть отдельным изделием или контактом. Зависит от контекста Э3.
4. Должен ли `quantity < len(entries)` быть отдельным замечанием.
5. Должен ли `duplicate_eligible` повышать `confidence` без проверки покрытия.
6. Точный состав 11 замечаний и значения `pe3_table_row`/`entries_count` в реальном XLSX.

## 13. Воспроизведение

```bash
git checkout diagnostics/kd-pe-105-baseline
.venv/bin/python -m pytest -q tests/test_kd_pe_105_diagnostics.py
.venv/bin/python -m pytest -q
```

Все данные синтетические. Реальные PDF, XLSX и конфиденциальные инженерные документы
в репозиторий не добавлялись. `main`, production, Supabase и EPLAN Checklist не изменялись.
