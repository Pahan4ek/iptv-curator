# iptv-curator

## Старт сессии
1. Прочитай ROADMAP.md и GOTCHAS.md (они короткие).
2. DECISIONS.md целиком не читай — смотри индекс вверху и открывай нужную запись, когда трогаешь эту область.
3. CHANGELOG.md не читай, только дописывай.

## Конец задачи
- CHANGELOG.md: одна строка в начало раздела текущей даты.
- ROADMAP.md: обнови "Где сейчас" и "Следующий шаг".
- Принял неочевидное решение или отказался от варианта → запись в DECISIONS.md.
- Наступил на грабли (баг API, странность библиотеки) → запись в GOTCHAS.md.
- Не плоди новые md-файлы без просьбы.

## Стек и команды
- Python, asyncio + aiohttp. Окружение `.venv` (в .gitignore, не коммитить) с версиями как в CI.
- Проверки: `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/pytest -v`. Нужен `pytest-asyncio` — добавь его в `.github/workflows/ci.yml` в том же PR или пиши async-тесты через `asyncio.run(...)`.

## Как работаем
- 1 PR = 1 issue: ветка от свежего `main`, коммиты по Conventional Commits, в PR `Closes #N`. Issue уже есть — новый не создавай.
- Каждый багфикс — с регрессионным тестом в `tests/`.
- Не ломай совместимость со старым `config.json` и CLI-флагами: у новых параметров безопасные дефолты.
- Отчёт по PR: изменённые файлы, результаты ruff/pytest, текст PR с `Closes #N`.

## Запреты
- Не коммить .env, ключи, seed-фразы. Не передавай их в сторонние модели.

## Agent skills
- Трекер задач: GitHub Issues этого репо (`gh`). Команды — `docs/agents/issue-tracker.md`.
- Метки: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. Нужной метки нет в репо — создай (`gh label create`).
- Решения — записи D### в DECISIONS.md, не `docs/adr/`. Термины — GLOSSARY.md (создаётся, когда согласован первый термин).
- ROADMAP — короткая карта: пункты ссылаются на issue или спеку, подробности туда не копируй.
