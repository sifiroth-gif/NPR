# Устройство NPR — действующее описание (25.11.2026)

## Контуры

| Контур | Источник и назначение |
| --- | --- |
| Живой HOT v2, Шики | main: код и правила; game-state/shiki: журнал и проекции |
| SQLite | rpg_engine/engine.py, campaign.sqlite: транзакционный ход, RNG, факты, поиск, закрытые сессии |
| Перенос | [SETTING_TRANSFER](SETTING_TRANSFER.md): чистый SQLite-шаблон; HOT требует адаптации |

[AGENTS](../AGENTS.md) на main и [GM_CORE](../campaigns/shiki/GM_CORE.md) — действующие правила ведущего. Справочники не заменяют их. Прямое указание игрока и действующие постоянные директивы выше правил. Приоритет фактов: последняя прямая поправка игрока → сыгранный первоисточник с последующими изменениями → неизменённый канон → карточка → производная сводка. Сводка — навигатор, не свидетельство.

## Живой ход

```
NEXT (1 чтение) → адресный источник при необходимости
→ preflight (заявка, источник, перспектива, цена, мир, дельта)
→ сцена → TURN (1 CAS-запись) → Actions → HOT, GM_ONLY,
  архив, карточки, индексы, GM_BRIEF, новый NEXT
```

- `recovery/NEXT.json` (npr-next/1): курсор, следующий ID, turn_file (SHA), lag, write, preflight, directives, capabilities, medical, state_sources, present/знания, кости, ресурсы, must_ids, must_pending, offscreen, arc, canon, checks, limits. Одно обычное чтение; важные изменяемые факты — последний меняющий первоисточник.
- `recovery/TURN.json` (npr-turn/2): дословная заявка и ответ, d.scene, дельта, review.predecessor. OOC не двигает часы. SHA — последняя успешная запись, при свежем старте NEXT.turn_file. При отставании только непрерывные подтверждённые записи; старые кости/ресурсы/must не повторять. Ошибка свёртки — тот же ID и amend; чужую запись не затирать. [FOLD_LAG](FOLD_LAG.md), [PREFLIGHT](PREFLIGHT.md).
- `recovery/HOT.json` (npr-hot/2): текущая сцена, курсор, открытые состояния, 10 строк хроники и 6 поправок канона (hot.py). Не редактируется ведущим.
- `recovery/GM_ONLY.json` (npr-gm-only/2) и файлы *GM_ONLY*: скрытая правда, планы, психика, обязательства. Наличие записи не означает доступность знания NPC/игроку.
- `recovery/GM_BRIEF.json`: оперативная сводка старта чата, не полный канон. memory/INDEX.md, memory/CANON.md, хроника — ориентиры.
- `archive/session-NNN-turns.jsonl`, `archive/deltas/session-NNN.jsonl`, `archive/turns/`, `archive/session-NNN-chronicle.md`: первичные записи и хроника. Не переписывать; отсутствующие дельты не реконструировать выдумкой.
- recovery/cast/, memory/npc/, archive/npc-memory/, archive/aged/: карточки, атрибутированные эпизоды, старение прошлых сессий. Текущая сессия не стареет.
- Служебные запросы по необходимости: RECALL.json, SESSION_CLOSE.json, MEMORY_TRIM.json, ZERO.json (только пробный прогон), не обычный ход.

Свёртка обрабатывает версии TURN в Git, включая пачку при отставании. Проверки прозы/источников не доказывают смысловую истинность. Структурный отказ и ошибка согласованности видны через NEXT/CHECKS. Игра не ждёт CI, но конфликт записи нельзя игнорировать.

## Подсистемы

| Механизм | Действующая реализация |
| --- | --- |
| Возможности и маршруты | capabilities.py, campaigns/shiki/ref/CAPABILITIES.json; текущая заявка |
| Источники и идентичность | [GENERAL_MEMORY](GENERAL_MEMORY.md), history_access.py, entity_memory.py, identity.py |
| Исторический поиск | heuristic_history.py, проверяемые шардированные указатели; необязательный meaning.py; кандидат требует источника |
| Директивы | [DIRECTIVES](DIRECTIVES.md), durable ops, действуют до отмены |
| Медицина | [MEDICAL_CONTEXT](MEDICAL_CONTEXT.md), medical.py, body.py, fight.py |
| Автономия | [WORLD_AUTONOMY](WORLD_AUTONOMY.md), will.py, news.py, obligations.py, arcs.py; с s14.text.024 проверка планов после каждого принятого игрового хода; ID вестей, отложенная очередь |
| Художественный контур | dread.py: индивидуальная реакция, hold/down, без квоты страха; GM_CORE, HORROR.md; prose_lint — эвристика |
| Preflight | [PREFLIGHT](PREFLIGHT.md): шесть пунктов вручную; CLI дополнительная проверка |
| Историческая приёмка | [HISTORICAL_REGRESSIONS](HISTORICAL_REGRESSIONS.md), изолированный снимок и хеши |

## Бюджеты

Единственный числовой источник — rpg_engine/limits.py. Байты для памяти, символы для инструкций.

| Показатель | Норма/цель | Предел |
| --- | ---: | ---: |
| HOT | 150000 | 180000 |
| GM_ONLY | 700000 (предупреждение) | не блокировка |
| GM_BRIEF | 60000 | 96000 |
| NEXT | 40000 | 50000 (при невозможности сжатия предупреждение) |
| AGENTS | — | 7000 |
| GM_CORE | — | 18000 |
| SKILL | — | 9000 |
| BOOT | — | 2400 |
| Сессия/чат | 120/50 ходов | рекомендации |

Остальные правила — RULES, предупреждения по limits.py, для правил только перерасход. [KEEPER](KEEPER.md) — межсессионное обслуживание.

## CI и публикация

main/.github/workflows/npr-tests.yml — тесты; npr-isolated-state-audit.yml — изолированный аудит (read-only, без публикации). game-state/shiki/.github/workflows/npr-live-memory.yml — действующий push-workflow: checkout main, fold, hot-check, публикация с повтором при гонке. Шаблон main не меняет workflow ветки состояния автоматически. Повторный recall_audit устранён в шаблоне main, действующая копия требует отдельного штатного обновления. Не запускать живую свёртку ради тестового хода. Замеры D2 в [MODERNIZATION_PLAN](MODERNIZATION_PLAN.md) разделяют connector read/write, поиск и Actions.

## Исторические форматы и восстановление

FAST_SCENE.json, docs/legacy и COMBAT_MECHANICS.md — история, не действующий регламент HOT. SQLite — отдельный поддерживаемый контур, не запасной файл сцены. Не откатывать игру заменой AGENTS и переходом к FAST_SCENE: это создаёт расходящиеся истории. Восстановление — точный снимок, проверка источников/курсора/хешей в изолированной копии; не переписывать живую историю. Смена сеттинга — отдельный пакет и ветка, SETTING_TRANSFER.
