"""Independent source, timeline, epistemic and world-state checks for the 2 July tower checkpoint.

Run only after rpg_engine.quick_save on a restored current game-state branch.
No state mutation. Ensures player retcons and actor-knowledge boundaries survive replay.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from rpg_engine import Campaign
from rpg_engine.engine import file_sha256


def check(state_dir: Path, request_path: Path) -> dict:
    source = json.loads(request_path.read_text(encoding="utf-8"))
    assert source["campaign_id"] == "naruto-shiki"
    assert source["expected_revision"] == 46
    turns = source["turns"]
    assert len(turns) == 21
    assert [x["id"] for x in turns] == [f"s01.{i:03d}" for i in range(47, 68)]
    cursor = "0001-07-02T08:57:00"
    for i, turn in enumerate(turns):
        assert turn["expected_revision"] == 46 + i
        assert turn["player_input"] and "Мастеру:" not in turn["player_input"]
        assert turn["text"].startswith("[Восстановление из сыгранного диалога]")
        assert turn["at"] >= cursor, f"time goes backwards at {turn['id']}"
        e_cursor = cursor
        for event in turn["events"]:
            assert e_cursor <= event["at"] <= turn["at"], (turn["id"], event["at"])
            e_cursor = event["at"]
            for operation in event.get("operations", []):
                if operation["op"] == "set_belief":
                    assert "source" in operation["belief"], event["id"]
                    assert operation["actor"] in event.get("witnesses", []) or (
                        operation.get("evidence") in ("reported", "inferred", "document")
                    ), event["id"]
        cursor = turn["at"]
    assert cursor == "0001-07-02T14:53:00"

    manifest = json.loads((state_dir / "state.json").read_text(encoding="utf-8"))
    game = Campaign(state_dir / "campaign.sqlite")
    verified = game.verify()
    assert verified["revision"] in (67, 68) and not verified["pending"]
    if verified["revision"] == 68:
        review = game.entry("event", "shiki.architecture-baseline.067", audience="gm")
        assert review["revision"] == 68 and review["at"] == cursor and review.get("review_sha")
    assert verified["at"] == cursor and verified["session"] == 1
    assert manifest == {"format": "text-rpg-git-state/1", **verified,
                        "sha256": file_sha256(state_dir / "campaign.sqlite")}
    assert source["instance"] == verified["instance"]
    ctx = game.context(audience="gm", recent=3)
    assert ctx["pending"] is None
    if ctx["next_process"] is not None:
        assert ctx["next_process"]["due_at"] > cursor, ctx["next_process"]
    for packet in turns:
        entry = game.entry("turn", packet["id"], audience="gm")
        assert entry["player_input"] == packet["player_input"] and entry["text"] == packet["text"]

    def fact(subject: str, name: str):
        rows = game.world(audience="gm", subject=subject)["facts"]
        return next(f["value"] for f in rows if f["name"] == name)

    exam = fact("exam", "stage")
    assert exam["finished"] == "0001-07-02T13:22:00"
    assert exam["elapsed_minutes"] == 382 and exam["verified"]
    sun = fact("exam", "suna_first_finish")
    assert sun["elapsed_minutes"] == 97 and sun["finish"].endswith("08:37:00")
    board = fact("exam", "tower_public_board_1355")
    assert len(board["passed_teams"]) == 2
    assert board["passed_teams"][0]["team"] == "Сунагакуре"
    report = fact("exam", "casualty_review")
    assert report["third_surrendered"] and report["third_killed_after_control"]
    assert report["disciplinary_decision"].startswith("НЕ ПРИНЯТО")
    assert report["stage2_status"].startswith("пройден")
    scrolls = fact("team.kusa", "scrolls")
    assert all(scrolls[k]["seal"] == "opened_inside_tower" for k in ("earth", "heaven"))
    tool_status = fact("shiki", "medical_tools_status")
    assert tool_status["chakra_sterilized"] and tool_status["damaged_senbon"] == 2
    fixes = fact("campaign", "retcons_tower_2026_11_12")
    assert len(fixes["invalidated"]) == 5
    gaara_retcon = fact("campaign", "gaara_scene_retcon")
    assert gaara_retcon["cutoff"].startswith("14:48")
    scene = fact("campaign", "current_scene")
    assert scene["time"] == "14:53" and scene["participants"] == ["shiki", "ren", "aki"]
    assert "gaara" in scene["absent"]
    gaa = game.world(audience="gm", subject="gaara")
    belief = next(x for x in gaa["beliefs"] if x["topic"] == "shiki.killed_today")
    assert belief["evidence"] == "inferred" and belief["certainty"] == "uncertain"
    for npc in ("takeru", "keiji", "haru"):
        assert game.world(audience="gm", subject=npc)["npc"] is not None
    assert fact("medical.case.keiji", "treatment")["restrictions"].startswith("без прыжков")
    return {
        "revision": verified["revision"], "session": verified["session"], "at": verified["at"],
        "turns_checked": len(turns), "board_teams": 2, "deaths_reported": 3,
        "suna_elapsed_minutes": 97, "kusa_elapsed_minutes": 382,
        "chakra_sterilization": True, "retcons_applied": True,
        "gaara_knowledge": "inferred, uncertain",
        "next_process": ctx["next_process"],
        "sha256": manifest["sha256"]
    }


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1]), Path(sys.argv[2])), ensure_ascii=False, indent=2))
