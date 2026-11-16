> **СПРАВОЧНИК ПО ТРЕБОВАНИЮ (16.11.2026).** Правила кампании — в `campaigns/shiki/GM_CORE.md`, краткая выжимка — в `campaigns/shiki/ref/`. Сюда заглядывать только за конкретной деталью. 

# NPC knowledge, source provenance and anti-metagaming in NPR

**Source architecture:** adapted to the compact NPR SQLite engine from `sifiroth-gif/Bleach-RPG-`: `docs/GM_PRE_SCENE_RECALL.md`, `docs/GM_NPC_AGENCY.md`, `world_autonomy.py`, `world_state.py`, `gm_scene_gate.py`, `npc_scene_fidelity.py` and `docs/GM_EVENT_GRAPH.md`. This is a functional adaptation, **not** a copy of Bleach campaign lore, private history, neural caches, or its entire heavyweight runtime.

## Root cause of the two actual leaks

On 1 July 1278 the GM presented *an already planned future rule* (Forest of Death scrolls) as if Aki had heard it; she had only heard that the second exam was at Training Ground 44 at 07:00 the next morning. It also made Ren refer to *the beam from a separate street incident*, although Ren and Aki were shopping elsewhere, did not witness the rescue and Shiki did not tell them. Both assertions arose because the GM merged global narrative knowledge, hidden scenario plans, player observations, and actor-specific knowledge.

These lines are **invalid for canonical NPC dialogue**:
- Aki: «По крайней мере пока не выясним, у какой команды нужный нам свиток».
- Ren: «А ещё мне бы не хотелось, чтобы наш медик снова получил балкой по плечу».

Do not alter the user's actions or erase the real events: the street rescue happened; only Ren's knowledge of its exact mechanism is denied. The existence of two scrolls remains a *GM expectation of future rules*, not current team knowledge. Do not have NPCs magically learn by inference after correction.

## Three-layer memory (provenance)

| Layer | Stored in NPR | What it proves |
| --- | --- | --- |
| Objective events and GM plans | `journal` and `facts`; private session/arc plans | What occurred or is planned. **Never sufficient proof of any NPC's knowledge.** |
| Personal knowledge / false beliefs of NPC | `beliefs(actor, topic, belief, evidence, certainty, source_event)` | What this **specific actor** believes; reported/inferred/document/witnessed and certainty preserved. A belief may be objectively false. |
| Source excerpts of played dialogue | `journal` with revisions + FTS5 `search`/`entry` | What the player/GM actually saw. Need review of **who was present, who learned, and which later correction supersedes it**. |

An event with `visibility=player_known` is not a broadcast to all NPCs. `gm_only` fact/plan may guide independent NPC actions **only if that NPC's own knowledge and capacities are sourced**. Knowing that a person is a medic is not knowledge of their secret kekkei genkai. Seeing a healed shoulder is not knowledge of being struck by a beam, nor of how that healing works.

## Acquisition protocol

When information is witnessed, told, read or plausibly inferred, record **individual** `set_belief` operations for each actual recipient *only after* the acquisition is played or source-confirmed. A non-witness does not receive the record simply for being a teammate. Use distinct topic IDs:
`exam.stage2.scrolls`, `shiki.city.beam_rescue`, `shiki.secret.zero_genome`, `orochimaru.current_operation` (high-risk policy topics);
plus situational topics such as `exam.registration_restored` and `hotel.intrusion_seen`.

Attach the `source_event` through the engine's world operation; `evidence` is one of `witnessed`, `reported`, `document`, `inferred`, `uncertain`; `certainty` one of `certain`, `probable`, `uncertain`. The operator must additionally check the event narrative, actual witnesses, time, access, and later contradictions. **Do not write fabricated source events to make a planned dialogue valid.** New source-annotated events now validate each actor's documented witness or report/document route. Historical legacy events without that metadata still require operator review.


### Explicit acquisition provenance for new events

In addition to `set_belief`, **new** NPR events may carry `witnesses` (stable actor IDs with direct first-hand access) and `knowledge_routes` (a named recipient and credible personal source of a report/document/inference). The event validator rejects actor-source mismatches, and `journal.details` preserves provenance for scene review. Old events remain readable; they are legacy/manual attestations and do not retroactively gain witnesses.

Example of a **synthetic, not played** report:

```json
{
  "id": "sample.message",
  "at": "0001-07-02T07:00:00",
  "summary": "Ведущий инструктажа вслух объяснил участникам правила.",
  "visibility": "player_known",
  "witnesses": ["aki", "ren", "shiki"],
  "knowledge_routes": [
    {"actor": "aki", "via": "reported", "source_actor": "anko", "source": "публично услышанный инструктаж Анко"},
    {"actor": "ren", "via": "reported", "source_actor": "anko", "source": "публично услышанный инструктаж Анко"}
  ],
  "operations": [
    {"op": "set_belief", "actor": "aki", "topic": "exam.stage2.scrolls",
     "belief": "Нужны определённые свитки", "evidence": "reported",
     "certainty": "certain", "visibility": "gm_only"},
    {"op": "set_belief", "actor": "ren", "topic": "exam.stage2.scrolls",
     "belief": "Нужны определённые свитки", "evidence": "reported",
     "certainty": "certain", "visibility": "gm_only"}
  ]
}
```

The source event shown above is **only an example**: at current rev31 it has NOT happened; do not import it into the campaign. A new live scene that grants knowledge without its own `witnesses` or `knowledge_routes` is rejected by the Shiki reviewed-turn pipeline. `reported` and `document` claims require an explicit matching route; mere presence at a location is not enough. `witnessed` requires the named personal witness. This adaptation mirrors the witness-checking principle of `Bleach-RPG-/world_autonomy.py`.

## Pre-scene rule: actor-first

1. Rebuild the scene: date/time/place, present actors, their **individual** goals and knowledge, what the player actually uttered, last unresolved questions. Open the current user correction before older GM plans.
2. For each important NPC reply, ask: **what exactly is this character using, and where/when/how did they learn it?** Verify their `world --audience gm --subject <actor>` / personal beliefs. If the only source is the player's perspective or GM-only arc plan, they cannot state it. They may sincerely but incorrectly infer an answer from an actually witnessed clue, with appropriate uncertainty.
3. Check if proposed assertions contradict the context of other NPCs. The actor could be deceptive, but a deliberate lie has its own goal and must not be a hidden route for GM metaknowledge.
4. Apply `campaigns/shiki/SCENE_PREP_PRIORITY.md` P0: physiological/body-language observations in **all conversations and battles**, psychological presence of Shiki in **every** scene. Observed physical signals are not guaranteed truth about thoughts.
5. In the **final** exact draft, run the source-bound audit `rpg_engine.knowledge_gate`. It recognizes signed dialogue, requires a reviewed line for every NPC utterance and matches protected high-impact keywords using `campaigns/shiki/KNOWLEDGE_POLICY.json`. A protected assertion requires a personally recorded belief and verified event. It rejects unsupported certainty, inconsistent review lines, missing speaker mapping, and cross-actor knowledge transfer; an invented leading question also needs a personally sourced premise.
6. Review nonprotected claims semantically as well: trigger lists are a **regression tripwire, not a mind-reading language model**. For a newly important secret, add a protected topic. Never use an invented belief to defeat the check.

## Verified future-turn pipeline

The canonical new turn preparation is `campaigns/shiki/maintenance/prepare_reviewed_turn.py`. It receives an envelope containing exact `turn` and separate private `knowledge_review`, checks it against **the live verified game-state** and P0 scene excerpts, then calls existing `stage_turn.main` on the unchanged turn packet. The save's existing `prepare`/`confirm` mechanism remains unchanged. The old file `maintenance/live_turn.json` is historical; do not replay it or use it as an escape hatch.

Example **synthetic** input:
```json
{
  "format": "NPR-SHIKI-REVIEWED-TURN/1",
  "turn": {
    "id": "sample.001", "expected_revision": 0, "at": "0001-07-01T10:00:00",
    "location": "Example room", "player_input": "#смотрю на часового#",
    "text": "**Дата:** ...\n**Время:** ...\n**День:** ...\n**Место:** ...\n\nЧасовой замедлил дыхание, увидев Шики.\n\n**Рэн:** — Пойдём."
  },
  "knowledge_review": {
    "format": "NPR-NPC-KNOWLEDGE-PREFLIGHT/1",
    "lines": [{"actor": "ren", "text": "Пойдём.", "claims": []}],
    "p0": {
      "shiki_presence": "Часовой замедлил дыхание, увидев Шики.",
      "conversation_body_language": "Часовой замедлил дыхание, увидев Шики.",
      "combat_present": false
    }
  }
}
```
The file is a **template, not an actual turn**. A line that makes a substantive protected claim needs a `claims` entry naming its topic and stance; the preflight demands personal source provenance. The exact dialogue and anchor excerpts must occur in the final `turn.text`. For ordinary NPC dialogue, `claims: []` is valid only when no tracked fact is implicitly asserted.

Trigger the maintained live-turn action only with a *new* `campaigns/shiki/maintenance/reviewed_live_turn.json` for the correct current revision. An absent envelope runs tests but does not prepare an old turn. Each outcome requires exact remote SQLite SHA verification. The in-game player's text is shown and confirmed through the existing prepare/confirm contract.

## Corrections and retroactivity

For a player correction to a prior GM mistake, retain the historical message as a **source transcript**, add a later sourced correction to canonical state and guard policies, and do not repeat the erroneous NPC assertion as truth. Distinguish historical artifact / accepted action / rejected statement. The game clock does not advance merely because a meta correction was issued.

## What was deliberately not ported

The other repository's 3-act FTS/embedding reranking, dynamic event graph over dozens of sessions, persona activation pipeline, whole WORLD_RUNTIME, multi-file release infrastructure and neural model caches address a substantially larger archive. Copying them now would introduce incompatible SQLite schemas and slow every turn. NPR already has FTS5 journal search, actor-scoped beliefs, structured world processes, and verified binary saves. Port the **epistemic constraints and executable scene gate**, then consider other retrieval systems only if actual history size demands them.

## Limitation

This gate is an **explicit-source and regression guard**, not perfect automatic semantic verification of arbitrary Russian dialogue. It can catch unsourced registered topics and omitted audit lines. It cannot prove that every `claims: []` line is truly innocuous or that the original GM-authored source event was credible. Therefore source review remains an obligatory GM task. A green test suite is not proof of literary quality or full narrative consistency.

## 12. Scope-of-knowledge — эпизод важнее места (прямой реткон игрока 12.11.2026)

Свидетель должен быть связан с **точным предметом** высказывания, а не просто с общим местом события. Два боя в Лесу смерти — разные эпизоды с разными множествами свидетелей. До реплики, содержащей «я видел», «что произошло», «в лесу», «там», «он», проверять ближайший референт разговора, конкретный `episode_id`, непосредственную видимость и `source_event`. Если в разговоре обсуждается Гаара, личное свидетельство Рэна о смерти шиноби Дождя не даёт ему права заявлять, что он видел, что Гаара делал в лесу. Если референт неоднозначен и не может быть подтверждён, формулировать конкретнее или отказать в заявлении, не приписывать знание.

Для каждого значимого заявления о персональном знании — audit record: `actor, quote, topic, event_id, witnessed|reported|inferred|unknown, source_event, recent_discussion_referent, scope_match`. `witnessed` требует наблюдения **того же события**. `reported` требует действительно переданного сообщения, `inferred` — наблюдаемой предпосылки и маркера неуверенности, `unknown` запрещает фактологическое утверждение. Авторское повествование о знании проверять так же строго, как диалог; `claims: []` не освобождает от смыслового аудита.

Применять `campaigns/shiki/continuity/NPC_WITNESS_MATRIX_JULY_1278.json` и его явные `known_non_witnesses`. Исправление 12.11.2026 аннулирует лишь ошибочную импликацию личного наблюдения Рэном **Гаары в лесу**, но не реальное свидетельство Рэна об убийстве сдавшегося дождевика Шики и не увиденное Реном с галереи нападение Гаары на Хинату.

### Исполняемый P0-11 / пример контракта

`rpg_engine.knowledge_gate.validate_scene` теперь вызывает `rpg_engine.referent_guard.scoped_errors`. Для каждого подписанного NPC-диалога со свидетельским глаголом `видел/слышал/наблюдал` мастер обязан передать структурированную ссылочную проверку. В `knowledge_review.scene_focus_entity` указывается текущая тема разговора (для обсуждения Гаары — `"gaara"`). В соответствующей строке `knowledge_review.lines[i].referent_audit` массив записей, **по одной на каждое предложение** с положительным свидетельским глаголом. Каждая запись: `quote` — точное целое предложение, `episode_id` — ID из `KNOWLEDGE_POLICY.json.episode_scopes`, `knowledge_type` — `witnessed/reported/document/inferred/unknown`, `source_event` — реальный личный источник. Передача `"shiki.rain_genin.forest_encounter"` как основания фразы «И видел, что произошло в лесу» при активной теме `gaara` отклоняется, даже несмотря на подлинное свидетельство Рэна о другом бое. Передача `"gaara.forest_death_actions"` c `witnessed` также отклоняется: Рэн в `known_non_witnesses`.

Это **преграда от формально проверяемых классов ошибок**, а не замена смыслового аудита ведущего: там, где нет выраженного свидетельского глагола, всё равно применять P0-11 и персональную матрицу. Код проверяется `tests/test_referent_guard.py`.
