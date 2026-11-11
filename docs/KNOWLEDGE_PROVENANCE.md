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

Attach the `source_event` through the engine's world operation; `evidence` is one of `witnessed`, `reported`, `document`, `inferred`, `uncertain`; `certainty` one of `certain`, `probable`, `uncertain`. The operator must additionally check the event narrative, actual witnesses, time, access, and later contradictions. **Do not write fabricated source events to make a planned dialogue valid.** The current engine records `source_event` but does not mechanically prove a character attended it.

## Pre-scene rule: actor-first

1. Rebuild the scene: date/time/place, present actors, their **individual** goals and knowledge, what the player actually uttered, last unresolved questions. Open the current user correction before older GM plans.
2. For each important NPC reply, ask: **what exactly is this character using, and where/when/how did they learn it?** Verify their `world --audience gm --subject <actor>` / personal beliefs. If the only source is the player's perspective or GM-only arc plan, they cannot state it. They may sincerely but incorrectly infer an answer from an actually witnessed clue, with appropriate uncertainty.
3. Check if proposed assertions contradict the context of other NPCs. The actor could be deceptive, but a deliberate lie has its own goal and must not be a hidden route for GM metaknowledge.
4. Apply `campaigns/shiki/SCENE_PREP_PRIORITY.md` P0: physiological/body-language observations in **all conversations and battles**, psychological presence of Shiki in **every** scene. Observed physical signals are not guaranteed truth about thoughts.
5. In the **final** exact draft, run the source-bound audit `rpg_engine.knowledge_gate`. It recognizes signed dialogue, requires a reviewed line for every NPC utterance and matches protected high-impact keywords using `campaigns/shiki/KNOWLEDGE_POLICY.json`. A protected assertion requires a personally recorded belief and verified event. It rejects unsupported certainty, inconsistent review lines, missing speaker mapping, and cross-actor knowledge transfer.
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
