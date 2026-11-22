# KD-PE-105: повторная проверка на Windows 11

Инструкция для независимой инженерной приёмки KD-PE-105 после блоков 1–5.
Она описывает **повторяемую проверку**, а не подтверждение того, что приёмка уже
выполнена. Реальные Э3/ПЭ3 и отчёт XLSX в репозиторий не добавляются.

## 1. Что проверяется

1. Контрольный сценарий TA: `3TA2` и `3TA3` по-прежнему обнаруживаются.
2. KM не даёт `warning` без независимого доказательства принадлежности строки.
3. X/XT не выдаются за самостоятельные изделия без контекста.
4. Excel/CSV сохраняют прежнюю структуру; история решений не меняется.
5. Полный pytest не содержит новых необъяснённых ошибок.

## 2. Подготовка окружения

Используйте отдельную тестовую папку, не рабочую установку.

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python -m playwright install chromium
```

Если используется `start_windows.bat`, он должен поднять локальный сайт только на
`127.0.0.1`; удалённый доступ не включается.

## 3. Проверка ветки и границ

```powershell
git fetch origin
git checkout feature/kd-pe-105-acceptance
git status --short
git log --oneline -6
```

Ожидается чистое рабочее дерево. Ветка `main` не должна быть изменена.
Реальные PDF, XLSX, `.env`, токены и локальные базы в индекс попадать не должны.

```powershell
git ls-files | Select-String -Pattern '\.(pdf|xlsx|xlsm|dxf|dwg)$'
git ls-files | Select-String -Pattern '(^|/)(\.env|\.env\.|.*\.pem|.*\.key)$|token|secret|password|credential'
```

Обе команды должны ничего не вывести.

## 4. Автоматические проверки

```powershell
.venv\Scripts\python tools\dependency_policy.py

.venv\Scripts\python -m pytest -q tests\test_identifier_classification.py tests\test_kd_pe_105_confidence_model.py tests\test_kd_pe_105_evidence.py tests\test_e3_pe3_row_matching.py tests\test_kd_pe_105_diagnostics.py tests\test_e3_pe3_inventory.py tests\test_e3_pe3_details.py

.venv\Scripts\python -m pytest -q tests\test_crosscheck.py tests\test_crosscheck_pdf_layout.py tests\test_document_identity.py tests\test_document_reference.py tests\test_pair_analysis.py tests\test_full_set_completeness.py tests\test_pe3_duplicates.py tests\test_finding_severity.py tests\test_engine.py tests\test_engine_rule_boundary_matrix.py tests\test_rule_evidence_catalog.py tests\test_kd_pe_100_evidence.py tests\test_e3_mixed_script_pages.py tests\test_e3_pe3_match_source_pages.py tests\test_pe3_quantity_row_provenance.py tests\test_pe3_unconfirmed_provenance.py tests\test_specification_reference_integration.py

.venv\Scripts\python -m pytest -q tests\test_parser_process.py tests\test_parser_worker_pdf_bundle.py tests\test_parser_disconnect.py tests\test_shared_parser_capacity.py tests\test_pdf_normalization.py tests\test_pdf_reader_limits.py tests\test_offline_document_analysis.py tests\test_synthetic_stand.py tests\test_acceptance_smoke.py

.venv\Scripts\python -m pytest -q tests\test_excel_reports.py tests\test_csv_limits.py tests\test_run_comparison.py tests\test_run_comparison_ui.py tests\test_scoped_history.py tests\test_scoped_history_multiprocess.py tests\test_history_backup.py tests\test_run_provenance.py tests\test_issue_management.py tests\test_e3_pe3_ui.py tests\test_secure_workbench.py

.venv\Scripts\python -m pytest -q
```

Ориентиры Linux-прогона на этом кандидате:

| Набор | Ожидаемый результат |
|---|---|
| Целевые тесты KD-PE-105 | 162 passed |
| Совместимость KD-PE-100…104 | 224 passed |
| PDF и изолированные парсеры | 73 passed, 1 skipped |
| Excel/CSV, история, UI | 104 passed |
| Полный pytest | 1080 passed, 1 skipped, 1 warning |

На Windows число `skipped` может отличаться: часть проверок относится к POSIX,
а Windows Job Object, наоборот, выполняется только на Windows. Ориентируйтесь
на отсутствие `failed` и на объяснимость каждого `skipped`. Единственный
известный Linux-skip — `tests/test_parser_process.py` (Windows Job Object
bootstrap only). Предупреждение Starlette/httpx существовало до изменений.

## 5. Синтетический контрольный стенд

```powershell
.venv\Scripts\python synthetic_stand.py accept --synthetic-only --workspace .\acceptance-stand
```

Стенд использует только синтетические данные и новый каталог. Результат
`synthetic_pass` подтверждает работоспособность контура, но не заменяет
инженерную проверку реальных документов.

## 6. Проверка реальных Э3/ПЭ3

1. Скопируйте реальные документы в папку **вне репозитория**.
2. Запустите `start_windows.bat` и откройте `http://127.0.0.1:8765`.
3. Загрузите Э3 и ПЭ3 одного обозначения выпуска.
4. Дождитесь завершения проверки и сохраните номер запуска.
5. Выгрузите XLSX и CSV в папку вне репозитория.
6. Заполните протокол из §8.

Для каждого замечания KD-PE-105 проверьте в интерфейсе и Excel:

- обнаруженные обозначения и их число;
- документ и подтверждённые листы Э3;
- документ, лист и строку ПЭ3 (если строка подтверждена);
- `reason`, `confidence`, основание достоверности;
- чего не хватило и какие кандидаты ПЭ3 указаны;
- рекомендацию инженеру.

## 7. Контрольные точки приёмки

- [ ] TA: `3TA2` и `3TA3` присутствуют в замечании, уровень `warning`,
      `reason=quantity_mismatch`, строка ПЭ3 подтверждена.
- [ ] KM: нет `warning` без независимого доказательства; при одной строке
      ожидается `info` и `row_matched_without_coverage` (или `row_ambiguous`
      при нескольких кандидатах), `confidence` не выше `medium`.
- [ ] X/XT: вывод, контакт или ссылка не выдаются за отсутствующее изделие;
      для клеммных семейств уровень не выше `info`.
- [ ] CSV: строго 6 колонок `Важность;Код;Файл;Место;Замечание;Рекомендация`.
- [ ] Excel: 10 базовых колонок; при наличии `details` добавлены только
      «Доказательства KD-PE-105» и «Детали KD-PE-105 (JSON)».
- [ ] История: решения инженера и журнал изменений сохраняются после
      повторного запуска и перезапуска сервера.
- [ ] Ни одно замечание не исчезло без объяснения; каждое изменение уровня
      отнесено к «устранено», «понижено», «перенесено в неопределённые» или
      «потеряно вследствие регрессии».

## 8. Протокол

Зафиксируйте локально и передайте без реальных документов:

| Пункт | Значение |
|---|---|
| Дата и ФИО проверяющего | |
| Windows 11 (сборка) | |
| Python | |
| Commit / ветка | |
| dependency_policy | |
| Полный pytest (passed/skipped/failed) | |
| Целевые KD-PE-105 | |
| Синтетический стенд | |
| Номер запуска на реальных Э3/ПЭ3 | |
| TA: `3TA2`, `3TA3` | |
| KM: уровень и `reason` | |
| X/XT: категория и уровень | |
| CSV: число колонок | |
| Excel: число колонок | |
| История решений | |
| Отклонения и задачи | |

Реальные PDF, XLSX, персональные данные и секреты в GitHub не публикуются.
При отклонении создаётся отдельная задача с обезличенным воспроизведением.

## 9. Границы

- Проверка не является аттестацией соответствия ЕСКД/ГОСТ.
- KD-PE-105 — текстовое сопоставление, а не OCR и не доказательство
  физического отсутствия элемента.
- Классификация и извлечение остаются эвристическими; интерфейс делает их
  ограничения явными.
- Успешный синтетический стенд и зелёный pytest не означают, что реальная
  инженерная приёмка уже выполнена.
