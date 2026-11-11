#!/usr/bin/env python3
"""Create a new verified Shiki campaign from the FULL approved dossier.

Run only before the first confirmed scene. Existing remote state is checked by CI
before publication, so an ongoing campaign cannot be silently reset.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from rpg_engine import Campaign
from rpg_engine.engine import file_sha256
from rpg_engine.git_state import FORMAT


HERE = Path(__file__).parent


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def fact(subject: str, name: str, value, visibility: str = "player_known"):
    return {"op": "set_fact", "subject": subject, "name": name,
            "value": value, "visibility": visibility}


def compile_setup(lore: dict, roster: dict, session: dict) -> dict:
    assert lore["campaign_id"] == "naruto-shiki"
    assert roster["schema"] == "naruto-shiki-roster/1"
    cards = roster["cards"]
    ids = [c["id"] for c in cards]
    assert len(ids) == len(set(ids)) and len(ids) == roster["character_count"]
    required = {"ren", "aki", "seina", "akihiro", "nao", "ibiki", "kabuto",
                "naruto", "sasuke", "sakura", "gaara", "orochimaru",
                "ame_faller", "registration_officer"}
    assert required.issubset(set(ids)), sorted(required.difference(ids))
    p = lore["protagonist"]
    assert p["age"] == 15 and p["affiliation"] == "Кусагакуре"
    assert sum(p["training_percent"].values()) == 100
    rules = {"canon_policy": lore["canon_policy"],
             "hero": {k: p[k] for k in (
                 "name", "age", "affiliation", "rank", "status", "physical", "unease",
                 "psychological_baseline", "natures", "stats_out_of_5",
                 "training_percent", "equipment", "skills", "zero_genome")},
             "narration": "Строгое следование campaigns/shiki/GM_PROTOCOL.md. "
                          "Дата, время, день и место в начале каждой новой сцены. "
                          "Все реплики подписаны. Слова и действия игрока литературно "
                          "встраиваются без изменения смысла, мира и контроля над Шики.",
             "session_planning": "До каждой сессии создавать адресный сюжетный "
                                 "план/ситуации в campaigns/shiki/sessions/. "
                                 "Это намерения, а не подтверждённые игровые события.",
             "source": "Chat_2026-11-11.pdf, всё утверждено игроком 11.11.2026"}
    npcs = [{
        "id": c["id"], "name": c["name"],
        "profile": {k: v for k, v in c.items() if k not in ("id", "name")}
    } for c in cards]

    operations = [
        fact("shiki", "approved_biography", p["biography"]),
        fact("shiki", "character_concept", {
            "name": p["name"], "japanese": p["japanese"], "age": p["age"],
            "village": p["affiliation"], "rank": p["rank"], "status": p["status"],
            "appearance": p["physical"], "psychology_as_starting_concept": p["psychological_baseline"],
            "unease": p["unease"], "future_not_predetermined": p["future_identity"]}),
        fact("shiki", "approved_training", {
            "percent": p["training_percent"], "ratings": p["stats_out_of_5"]}),
        fact("shiki", "starting_techniques", p["skills"]),
        fact("shiki", "zero_genome", p["zero_genome"]),
        fact("shiki", "weapons", p["equipment"]),
        fact("shiki", "relations", {
            "father": "akihiro", "mother": "nao", "medical_teacher": "seina",
            "teammates": ["ren", "aki"], "field_commander": "kusa_leader"}),
        fact("world", "setting", lore["canon_policy"]["setting"]),
        fact("exam", "stage", {"phase": "допуск к письменному экзамену",
                               "location": session["start"]["location"],
                               "start_time": session["start"]["time"]}),
        fact("exam", "involved_teams", {
            "grass": ["shiki", "ren", "aki"],
            "team7": ["naruto", "sasuke", "sakura"],
            "team10": ["shikamaru", "ino", "choji"],
            "team8": ["kiba", "hinata", "shino", "akamaru"],
            "team_gai": ["neji", "lee", "tenten"],
            "sand": ["gaara", "temari", "kankuro"],
            "sound": ["dosu", "zaku", "kin"],
            "kabuto": ["kabuto", "yoroi", "misumi"],
            "rain_oboro": ["oboro", "mubi", "kagari"],
            "rain_shigure": ["shigure", "baiu", "midare"],
            "rain_reigo": ["ame_faller", "ame_ally1", "ame_ally2"]}),
        fact("world", "canon_status", {
            "approved": "Все данные PDF, приоритет последних исправлений; биография и NPC больше не черновики",
            "future_development": "Ненаступившие события и техники будущего не считаются освоенными",
            "play": "Игровых ходов до opening.001 не было"}),
    ]
    for item in session["world_clocks"]:
        operations.append({
            "op": "start_process", "id": item["process_id"], "owner": item["owner"],
            "goal": item["intention"], "due_at": item["due"], "visibility": "gm_only",
            "notes": "Оперативное намерение; результат пока не наступил. "
                     "Решать в момент срока в зависимости от состояния мира."
        })
    return {
        "id": lore["campaign_id"], "title": lore["title"],
        "start_at": lore["calendar"]["engine_start_at"],
        "location": session["start"]["location"],
        "player": {"id": p["id"], "name": p["name"]},
        "rules": rules, "npcs": npcs, "operations": operations,
    }


def build(output: Path):
    output.mkdir(parents=True, exist_ok=True)
    for file in ("campaign.sqlite", "state.json"):
        if (output / file).exists():
            raise FileExistsError(f"Refusing to overwrite existing build: {output / file}")
    lore = read_json(HERE / "lore.json")
    roster = read_json(HERE / "roster.json")
    session = read_json(HERE / "sessions" / "SESSION_001.json")
    setup = compile_setup(lore, roster, session)

    with tempfile.TemporaryDirectory(prefix="npr-shiki-bootstrap-") as tmp:
        local = Path(tmp) / "working.sqlite"
        game = Campaign.create(local, setup)
        initial = game.verify()
        assert initial["revision"] == 0 and not initial["pending"], initial
        draft = game.prepare(session["opening_turn"])
        assert draft["text"] == session["opening_turn"]["text"]
        checked = game.verify()
        assert checked["revision"] == 0 and checked["pending"] is True, checked
        saved = game.checkpoint(output / "campaign.sqlite")
        assert saved["ok"] and saved["pending"] and saved["revision"] == 0, saved

    db = output / "campaign.sqlite"
    check = Campaign(db)
    ctx = check.context(audience="gm")
    pending = ctx["pending"]
    assert pending and pending["turn_id"] == "opening.001", pending
    assert pending["draft_sha"] == draft["draft_sha"]
    assert pending["packet"]["text"] == session["opening_turn"]["text"]
    assert len(check.world(audience="gm")["npcs"]) == len(roster["cards"])
    assert not ctx["recent"], "The opening must remain UNCONFIRMED until shown in chat"
    manifest = {"format": FORMAT, **check.verify(), "sha256": file_sha256(db)}
    (output / "state.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "result": "verified", "manifest": manifest, "roster": len(roster["cards"]),
        "future_processes": len(session["world_clocks"]),
        "pending_draft_sha": pending["draft_sha"],
        "starting_scene": session["opening_turn"]["id"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.output)
