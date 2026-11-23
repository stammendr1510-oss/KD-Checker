# KD-PE-107: исправления Windows-приёмки

Дата: 23.11.2026. Скрипт:
[`tools/windows11_acceptance.ps1`](../tools/windows11_acceptance.ps1).
Статус фактического прогона на Windows 11: **NOT VERIFIED**.

## 1. Дефекты

| ID | Приоритет | Симптом | Причина |
|---|---|---|---|
| KD107-04 | P2 | Windows PowerShell 5.1 неверно читает скрипт, ошибки разбора | UTF-8 без BOM с кириллицей; 5.1 читает файл как ANSI |
| KD107-05 | P2 | `git clone` падает с «destination path '.' already exists and is not an empty directory» | скрипт создавал `WorkDir`/логи до клонирования |

## 2. Исправление KD107-04 (кодировка)

- Скрипт сделан **ASCII-only**: ни одного не-ASCII символа, включая
  комментарии, сообщения и ожидаемый заголовок UI.
- Ожидаемый заголовок страницы собирается из кодов символов:

```powershell
$expectedTitle = -join ([char[]](0x041F, 0x0440, 0x043E, 0x0432, 0x0435,
                                 0x0440, 0x043A, 0x0430, 0x0020, 0x041A, 0x0414))
```

- Добавлена явная синтаксическая проверка через
  `[System.Management.Automation.Language.Parser]::ParseFile(...)`:
  ошибки разбора останавливают прогон до выполнения шагов.
- `Write-StepResult` и логи не содержат кириллицы.

Проверки: `test_script_is_pure_ascii_for_windows_powershell_51`,
`test_no_cyrillic_literal_in_script`, `test_script_has_an_explicit_syntax_check`,
`test_powershell_parser_if_available`.

## 3. Исправление KD107-05 (безопасный клон)

- Логи и JSON-сводка всегда пишутся в отдельный `ResultsDir`
  (по умолчанию `<WorkDir>-results`), **вне** исходного каталога.
- До `git clone` в `WorkDir` ничего не создаётся.
- `Test-WorkDirIsEmpty()` проверяет каталог:
  - не существует → клонирование разрешено;
  - существует и пуст → клонирование разрешено;
  - существует и не пуст → остановка с сообщением, **ничего не удаляется**.
- `-SkipClone` разрешает повторный запуск на существующем checkout; SHA всё
  равно проверяется.
- Клон выполняется как `git clone --no-checkout`, затем `git fetch --all
  --prune`, затем точный `git checkout --detach <SHA>`.

Проверки: `test_results_are_outside_the_clone_directory`,
`test_clone_does_not_require_a_precreated_workdir`,
`test_script_has_no_destructive_cleanup`.

## 4. Контракт скрипта

| Параметр | Назначение | По умолчанию |
|---|---|---|
| `-RepoUrl` | репозиторий | `https://github.com/stammendr1510-oss/KD-Checker.git` |
| `-ExpectedSha` | точный коммит | `385db15087afca3a8db5dca5573846aef02f9710` |
| `-WorkDir` | отдельный каталог исходников | `C:\KD-Checker-acceptance` |
| `-ResultsDir` | каталог логов и сводки | `<WorkDir>-results` |
| `-PythonCommand` | запуск Python | `py -3.13` |
| `-SkipClone` | использовать существующий checkout | выключен |

Шаги:

1. `environment` — версия PowerShell и ОС (INFO).
2. `git_clone` — безопасный клон.
3. `git_checkout_exact_sha` — `git cat-file -e`, `checkout --detach`,
   сверка `rev-parse HEAD`, `git status --short`.
4. `python_venv` — изолированный venv, зависимости.
5. `dependency_policy`.
6. `targeted_kd_pe_107_tests`.
7. `full_pytest`.
8. `engineering_acceptance_stand`.
9. `kd_pe_107_reference_selfcheck`.
10. `application_launch_1/2` — два запуска, проверка `http://127.0.0.1:8765`
    и заголовка страницы, корректная остановка процессов.

Скрипт не выполняет merge, не делает push, не удаляет непустые каталоги и не
изменяет рабочую историю.

## 5. Коды выхода

| Код | Условие |
|---|---|
| `0` | все выполненные шаги PASS |
| `1` | хотя бы один шаг FAIL |
| `2` | нет FAIL, но есть `NOT_VERIFIED` |

`NOT_VERIFIED` никогда не превращается в `PASS`. Проверка:
`test_exit_codes_are_distinct`.

## 6. JSON-сводка

Файл `<ResultsDir>/windows11-acceptance-summary.json`:

```json
{
  "kind": "kd-checker-windows11-acceptance-v2",
  "verdict": "PASS | FAIL | NOT_VERIFIED",
  "expected_sha": "...",
  "work_dir": "...",
  "results_dir": "...",
  "platform": "...",
  "powershell": "...",
  "python_command": "...",
  "generated_at": "...",
  "failed_steps": 0,
  "not_verified_steps": 0,
  "steps": [],
  "note": "Windows 11 acceptance is valid only for the exact expected_sha above."
}
```

Сводка привязана к конкретному `expected_sha`; результат другого коммита не
переносится.

## 7. Что не проверено

- Фактический запуск на Windows 11 и Windows PowerShell 5.1 — **NOT VERIFIED**:
  среды нет, доступен только статический анализ и `pwsh`, если установлен.
- Тест `test_powershell_parser_if_available` пропускается, если `pwsh`
  недоступен; skip не считается успешной проверкой.
- Поведение UI на разных разрешениях и DPI не проверялось.

## 8. Как выполнить приёмку

```powershell
powershell -ExecutionPolicy Bypass -File tools\windows11_acceptance.ps1 `
    -WorkDir C:\KD-Checker-acceptance `
    -ResultsDir C:\KD-Checker-acceptance-results `
    -PythonCommand "py -3.13"
```

После прогона:

1. проверить `verdict` в JSON-сводке;
2. при `FAIL` открыть лог упавшего шага в `<ResultsDir>\logs`;
3. при `NOT_VERIFIED` выполнить пропущенный шаг вручную и повторить;
4. зафиксировать `expected_sha` и `generated_at` в протоколе приёмки.

Только прогон с `verdict = PASS` и точным `expected_sha` считается
подтверждением для Windows 11.
