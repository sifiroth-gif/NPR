"""Source-checked S08 -> S09 handoff for NPR Shiki.

The source is the already-accepted S08 JSONL, NOT a generated retelling.
96 played turns are entered into the cold SQLite journal verbatim; 10 OOC
corrections remain in the source/index. No new in-world action is executed.
Live HOT/NEXT remain the per-turn authority; a session close merely pins them.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from rpg_engine import compaction, git_state, hot, live_scene, nextpack, scene_gate, sessions, verification
from rpg_engine.engine import Campaign, file_sha256

SOURCE = "archive/session-008-turns.jsonl"
EXPECTED_SOURCE_BLOB = "74d7deaec701c2c32cd8f55365bfbe556c67d753"
START = "0006-10-03T01:35:00"
END = "0006-10-09T16:17:00"
LAST = "s08.text.106"
FIRST_NEXT = "s09.text.001"
HANDOFF = "campaigns/shiki/continuity/SESSION_008_TO_009_HANDOFF_1283-10-09.json"
INDEX = "archive/session-008-index.json"

def blob(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def source_checked_rows(root):
    data = (root / SOURCE).read_bytes()
    if blob(data) != EXPECTED_SOURCE_BLOB:
        raise ValueError("S08 source changed since review: " + blob(data))
    rows = [json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != 106:
        raise ValueError(f"Expected 106 exact S08 records, got {len(rows)}")
    for n, row in enumerate(rows, 1):
        if row.get("id") != f"s08.text.{n:03}":
            raise ValueError("S08 source gap/duplicate at " + str(n))
        if not START <= row["at"] <= END:
            raise ValueError("S08 source out of range: " + row["id"])
        if row.get("kind") != "ooc" and not (row.get("gm") and row.get("place")):
            raise ValueError("Empty played scene " + row["id"])
    if sum(row.get("kind")=="ooc" for row in rows) != 10:
        raise ValueError("OOC count changed (expected 10)")
    last = rows[-1]
    if last["id"] != LAST or last["at"] != END or last.get("kind")=="ooc":
        raise ValueError("S08 accepted terminal turn changed")
    if "Кабуто" not in last["gm"] or "Орочимару" not in last["gm"] or \
       "предлож" not in last["gm"]:
        raise ValueError("S08 narrative terminal scene is not the Kabuto offer")
    return rows, hashlib.sha256(data).hexdigest()

def assert_live_terminal(root, rows):
    last = read_json(root/"recovery/TURN.json")
    if last["id"] != LAST or last["at"] != END or last.get("gm") != rows[-1]["gm"]:
        raise ValueError("TURN.json changed or does not match accepted S08 source")
    folded = hot.fold(root)
    if folded.get("errors") or folded.get("cursor") != LAST:
        raise ValueError("S08 fold not current: " + str(folded))
    live = read_json(root/"recovery/HOT.json")
    if live["cursor"].get("id") != LAST or live["cursor"].get("at") != END or \
       live.get("last",{}).get("gm") != rows[-1]["gm"]:
        raise ValueError("HOT.json does not match exact accepted S08")
    nxt = read_json(root/"recovery/NEXT.json")
    if nxt["cursor"] != LAST or nxt.get("fold_errors"):
        raise ValueError("NEXT.json contains unresolved fold error or different turn")
    return live,nxt

def verify_beginning(root, game):
    s = game.verify()
    if (s["session"],s["revision"],s["at"],s["pending"]) != (8,497,START,False):
        raise ValueError("Unexpected initial cold S08 checkpoint: " + str(s))
    git_state.verify_manifest(read_json(root/"state.json"), game.path)

def replay(root, game, rows, digest):
    memory_path = root / "recovery/SCENE_MEMORY_INDEX.json"
    memory = read_json(memory_path)
    old_cursor = memory.get("cursor",{}).get("turn",LAST)
    memory_sha=blob(memory_path.read_bytes())
    index=[]
    count=0
    for n,t in enumerate(rows,1):
        ref = f"{SOURCE}#{t['id']}"
        if t.get("kind")=="ooc":
            index.append({"id":t["id"],"status":"ooc","at":t["at"],"source":ref})
            continue
        state=game.verify()
        if t["at"]<state["at"]:
            raise ValueError("Time regression at "+t["id"])
        with game.connection() as con:
            predecessor = con.execute(
                "SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1"
            ).fetchone()["id"]
        # Every SQLite replay packet is one already-played line, not a newly
        # narrated action. The source journal contains the OOC corrections.
        packet={
            "id": f"s8.played.{n:03}",
            "expected_revision":state["revision"],
            "at":t["at"],"location":t["place"],
            "player_input":t["player"],"text":t["gm"],
            "input_mode":"direction",
            "events":[],
            "source_ref":ref,"source_status":"exact",
            "memory_gate":{
                "schema":"npr-memory-gate/1",
                "previous_turn_id":predecessor,
                "index_turn":old_cursor,"index_blob_sha":memory_sha,
                "player_input":t["player"],
                "source_refs":[{"source":SOURCE,"sha256":digest}],
                "unretrieved":[],"mode":"source_locked_intersession_replay",
            },
        }
        if n==len(rows):
            packet["scene"]={
                "id":"s9.kagen.lab.1617",
                "actors":["shiki"],
                "beat":"conflict",
                "focus":"Kabuto offered cooperation and said Orochimaru has been informed; Shiki has not accepted.",
            }
        try:
            receipt=game.prepare(packet)
            game.confirm(packet["id"],receipt["draft_sha"])
        except Exception as exc:
            raise RuntimeError(f"Cold S08 replay FAILED at {t['id']} (SQLite revision {state['revision']})") from exc
        count+=1
        index.append({"id":t["id"],"status":"played","sqlite":packet["id"],
                      "at":t["at"],"source":ref})
    if count!=96:
        raise ValueError(f"Expected 96 played entries, got {count}")
    return index,count

def handoff(root,rows,sha,closed,sqlite_state,index):
    save(root/INDEX,{
      "format":"npr-shiki-session8-replay/1",
      "source":SOURCE,"source_blob":EXPECTED_SOURCE_BLOB,"source_sha256":sha,
      "count":len(rows),"played":96,"ooc":10,"last_turn":LAST,
      "terminal":END,"sqlite_revision":sqlite_state["revision"],
      "archive_sha256":closed["sha256"],"index":index,
      "source_conventions":"The original chat-export is a supplied secondary review; the immutable accepted archive/session-008-turns.jsonl is the exact source. Earlier overwritten attempts are not reintroduced.",
    })
    save(root/HANDOFF,{
      "format":"npr-shiki-intersession-handoff/1",
      "closed_session":8,"next_session":9,
      "sqlite_revision":sqlite_state["revision"],"at":END,
      "last_live_turn":LAST,"first_live_turn":FIRST_NEXT,
      "source":INDEX,"player_choice":"unresolved",
      "scene":"Otogakure, Kagen hidden research facility, third level, laboratory: Shiki and Kabuto.",
      "established":[
        "Shiki crossed into the Sound territory voluntarily as a self-proclaimed nukenin after leaving Kusagakure. The Kusa plot continues autonomously without his presence.",
        "Kabuto recognizes Shiki from the old examination period, knows of prior observations, and offers equipment, workspace and material in return for independent research results.",
        "A newly arrived adult subject (~40, metallic neck band and injured left hand) was intended for Kabuto, not for Shiki; the low frequency tone triggered a shared involuntary pattern in patient and isolated muscle, with a delay after shutdown.",
        "Kabuto ordered the sound stimulation stopped and had the patient moved to the medical section; the clinical mechanism and patient's full origin are NOT established.",
        "Kabuto says he informed Orochimaru. The content and delivery of this message are not independently confirmed by Shiki.",
        "Shiki has NOT accepted a contract, ceded tissue, agreed to exclusive service or met Orochimaru.",
        "The Pact remains at the latest player-corrected stage 6 with whole skin and no flaking. The unusually dark blood and direct-contact risk are known only to people with a source.",
        "Nemera retains granted sensory access, but cannot read Shiki's private thoughts or direct his actions.",
        "Do not turn the live narrative into a bureaucratic negotiation; Shiki's presence visibly affects people without a magical aura.",
      ],
      "live_next_must":"Read recovery/NEXT.json freshly at s09.text.001; must/offscreen are authoritative and were NOT resolved by intersession maintenance.",
      "still_open":[
        "Kabuto's proposal and any decision by Orochimaru.",
        "The Kagen subject and the nontrivial sound/muscle response.",
        "Independent pursuit and archival fallout after Shiki declared himself a nukenin.",
        "Kusa V-7 residual evidence, medical samples, family/medical confidentiality and source of prior personalized orders.",
        "Nemera's autonomous plan and Minase's next intention, to be resolved from NEXT.must without granting Shiki their secrets.",
      ],
      "quality":["Do not invent hidden causal links between Sound experiments and Kusa S-17.",
                 "Limit OOC speculation about NPC motives; record sources of NPC knowledge.",
                 "Critical actions must have mechanical and dramatic consequences. Do not defer scene-critical choices indefinitely.",
                 "Do not assign Shiki's intentions, speech or commitments.",
                 "Unclosed scene clocks advance only by world time with source-backed outcomes.",
                 "Distinguish a decision the NPC has made from a proposed action they have not taken."],
    })

def derived_memory_close(root, sqlite_revision):
    state, _=hot._read_state(root)
    if state["cursor"]["id"] != LAST or state["cursor"]["at"] != END:
        raise ValueError("HOT cursor moved before closing session")
    state["_undo"]=None
    state["handoff"]={
       "closed":8,"next_session":9,"sqlite_revision":sqlite_revision,"at":END,
       "last_turn":LAST,"first_turn":FIRST_NEXT,"source":HANDOFF,
    }
    # The game clock and the NPC clocks are untouched. The last scene remains
    # live for the new chat; no scene or player choices are fabricated.
    hot.refresh(state)
    hot._write_state(root,state)
    _, accepted=scene_gate.played_turns(root)
    save(root/live_scene.PATH,live_scene.build_for_state(state,accepted,root))
    hot._write_checks(root,state)
    checks=hot.check(root)
    if not checks.get("ok"):
        raise ValueError("S08 closed checkpoint HOT check failed "+str(checks))
    packet=nextpack.write(root,state,read_json(root/live_scene.PATH),
                          read_json(root/"recovery/CHECKS.json"))
    if packet.get("next_id") != FIRST_NEXT or packet.get("fold_errors"):
        raise ValueError("NEXT.json failed S09 handoff: "+str(packet.get("next_id")))
    return checks

def main(root):
    root=Path(root)
    rows,source_sha=source_checked_rows(root)
    game=Campaign(root/"campaign.sqlite")
    current=game.verify()
    if current["session"]==9 and current["at"]==END:
        if not (root/INDEX).is_file() or not (root/HANDOFF).is_file():
            raise ValueError("S09 already opened but source audit artifacts missing")
        state,_=hot._read_state(root)
        if state["cursor"]["id"]!=LAST:
            raise ValueError("New play begun: cannot refresh S09 from old source")
        _,accepted=scene_gate.played_turns(root)
        save(root/live_scene.PATH,live_scene.build_for_state(state,accepted,root))
        nextpack.write(root,state,read_json(root/live_scene.PATH),
                       read_json(root/"recovery/CHECKS.json"))
        if read_json(root/nextpack.PATH).get("next_id") != FIRST_NEXT:
            raise ValueError("Previously closed S09 has wrong next ID")
        print("S08_ALREADY_CLOSED",json.dumps(current,ensure_ascii=False),flush=True)
        return
    assert_live_terminal(root,rows)
    verify_beginning(root,game)
    index,played=replay(root,game,rows,source_sha)
    if game.verify()["at"]!=END:
        raise ValueError("Cold replay stopped before final played time")
    closed=sessions.close(game,game.verify()["revision"],root/"archive/session-008.md")
    audit=verification.audit(game)
    if audit["session"]!=9 or audit["at"]!=END:
        raise ValueError("S08->S09 invalid SQLite transition "+str(audit))
    with game.connection() as con:
        count=con.execute("SELECT count(*) FROM journal WHERE session=8 AND kind='turn'").fetchone()[0]
        if count!=played:
            raise ValueError(f"SQLite session 8 has {count} turns, source has {played}")
        if not compaction.audit(con,root):
            raise ValueError("Source-locked S08 compaction failed")
    handoff(root,rows,source_sha,closed,audit,index)
    checked=derived_memory_close(root,audit["revision"])
    state={"format":git_state.FORMAT,**game.verify(),"sha256":file_sha256(game.path)}
    save(root/"state.json",state)
    shutil.rmtree(root/"recovery/sqlite",ignore_errors=True)
    git_state.write_transport(root,state)
    git_state.verify_manifest(state,game.path)
    print("S08_TO_S09_SUCCESS",json.dumps({"session":state["session"],"revision":state["revision"],
        "at":state["at"],"played":played,"ooc":len(rows)-played,
        "source_sha256":source_sha,"archive_sha256":closed["sha256"],
        "hot":checked},ensure_ascii=False),flush=True)

if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("Usage: recover_session8_20261120.py PATH_TO_PRIVATE_WORKTREE")
    main(Path(sys.argv[1]))
