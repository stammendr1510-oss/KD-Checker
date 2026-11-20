# Базовая линия стабилизации

Зафиксировано перед изменениями инфраструктуры. Относится к итерации 1
плана [`plans/stabilization-roadmap.md`](../plans/stabilization-roadmap.md).

## Ревизия и окружение

| Параметр | Значение |
|----------|----------|
| Коммит | `b97729e2451caad282240e181b959c70549a125f` |
| Ветка | `main` |
| ОС аудита | Linux 5.15 |
| Python | 3.14.2 |
| Способ запуска | локальное окружение `.venv` |

Версия приложения: `0.2.0` (`app.app.version`).

### Версии прямых зависимостей

| Пакет | Версия |
|-------|--------|
| fastapi | 0.165.0 |
| starlette | 1.10.0 |
| pydantic | 2.14.4 |
| uvicorn | 0.54.0 |
| python-multipart | 0.0.34 |
| PyMuPDF | 1.28.2 |
| openpyxl | 3.1.5 |
| ezdxf | 1.4.5 |
| pytest | 9.2.0 |
| httpx | 0.28.1 |
| playwright | 1.65.2 |

## Результаты прогонов

### Тесты без браузерного модуля

```console
.venv/bin/python -m pytest -q --ignore=tests/test_issue_ui.py
```

Результат: **355 passed, 1 warning** за 6.83 с.

Предупреждение: `StarletteDeprecationWarning` о переходе с `httpx` на `httpx2`
в `starlette.testclient`. На результат не влияет.

### Полный набор тестов

```console
.venv/bin/python -m pytest -q
```

Результат: **5 failed, 355 passed, 1 warning** за 10.04 с.

Все пять падений — `tests/test_issue_ui.py`:

- `test_engineer_workflow_history_recheck_conflict_and_export`;
- `test_russian_report_cards_responsive_history_and_exports[360-800]`;
- `test_russian_report_cards_responsive_history_and_exports[768-1024]`;
- `test_russian_report_cards_responsive_history_and_exports[1366-768]`;
- `test_russian_report_cards_responsive_history_and_exports[1920-1080]`.

Причина: не установлен браузер Chromium для Playwright
(`playwright install`). Это подтверждает находку F3: браузерный контур
является жёсткой локальной зависимостью и должен пропускаться, а не падать.

## Состояние рабочей копии

После прогонов:

```console
git status --porcelain
```

```text
?? .codeassistant/
?? __pycache__/
?? plans/
?? tests/__pycache__/
```

Каталог `data/` не создан: тесты изолированы `monkeypatch` на уровне
отдельных модулей, глобальной фикстуры пока нет.

## Локальные артефакты, отсутствующие в git

| Артефакт | Назначение | Почему отсутствует |
|----------|------------|--------------------|
| `.venv/` | виртуальное окружение | создаётся при установке зависимостей |
| Chromium Playwright | браузерные тесты | устанавливается командой `playwright install` |
| `data/history.sqlite3` | история проверок | создаётся приложением при первом запуске |
| `config.local.json` | пользовательские настройки | появится в итерации 6, должен быть вне git |

## Что подтверждено

- дерево файлов совпадает с `main` GitHub-репозитория на том же коммите;
- все 18 runtime-модулей и 26 тестовых модулей присутствуют;
- локальные импорты разрешаются, отсутствующих модулей нет;
- небраузерная часть набора зелёная на чистом окружении;
- единственная фактическая поломка сборки — отсутствие браузера.
