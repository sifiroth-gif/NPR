"""Source-locked S7 -> S8 handoff. No new fiction or autonomous outcomes.

Replays the *accepted exact live turns* already in the private S7 JSONL;
OOC corrections remain in the original source and in the post-session index.
SQLite captures source-backed canonical milestones, but HOT stays the richer
per-turn story projection, never overwritten from the old cold checkpoint.
"""
from __future__ import annotations
import hashlib
import json
import shutil
import sys
from pathlib import Path

from rpg_engine import compaction, git_state, hot, live_scene, scene_gate, sessions, verification
from rpg_engine.engine import Campaign, file_sha256

SOURCE = "archive/session-007-turns.jsonl"
EXPECTED_SOURCE_BLOB = "bcb73d5ada3455f4f62f3c2e630cbd38363a90f8"
BEGIN = "0006-10-02T22:47:00"
END = "0006-10-03T01:35:00"
LAST = "s07.text.038"
HANDOFF = "campaigns/shiki/continuity/SESSION_007_TO_008_HANDOFF_1283-10-03.json"
INDEX = "archive/session-007-index.json"

def blob(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def save(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def fact(name, value, *, gm_only=False):
    return {"op":"set_fact", "subject":"case_19-T", "name":name,
            "value":value, "visibility":"gm_only" if gm_only else "player_known"}

# The date, witness, evidence, limitations and later corrections are already
# played in SOURCE. Adding a milestone must NEVER add information to the scene.
MILESTONES = {
    5: [fact("s7_stage3_contact", {"contact":"temporary grey skin and branching dark vascular marks",
                                   "stopped":"reverses on separation; not a fifth-stage infection",
                                   "correction_source":"s07.text.006"})],
    17: [fact("s7_arakawa_biopsies", {"samples":4, "places":"right graft and interface; left control",
                                     "research":"Shiki transplanted samples into separate pockets of own left upper-arm muscle",
                                     "canonical_results":"superseded by s07.text.019 and succeeding played tests"})],
    20: [fact("s7_arakawa_donor_signalling", {
        "source":"canonical replacement of s07.text.017 reported in s07.text.019, plus s07.text.020",
        "cause":"living graft cells have distinct donor genome and independent pathological signalling",
        "valid_ability":"Zero Genome can deliberately absorb and functionally assay foreign genomic programs in own tissues",
        "limitation":"Do not assert donor identity from this alone"})],
    25: [fact("s7_research_carrier", {"at":"0006-10-02T23:56:00",
         "status":"experimentally demonstrated on donor biopsies, distinct from patient treatment",
         "target":"reduce pathological nsp-delta expression while preserving treated cell viability",
         "nature":"limited fictional chakra carrier; not automatically self-propagating",
         "nemera":"Temporary one-way Zero Genome sensory access then closed; other earlier sensory access retained"})],
    27: [fact("s7_arakawa_trial", {"at":"0006-10-03T00:47:00",
         "result":"partial clinical improvement: fewer involuntary contractions, purposeful motion and perfusion retained",
         "not_proven":"complete cure, whole-graft modification, independence from S-17",
         "biopsies":"all four destroyed within Shiki; left upper arm recovered",
         "shiki_consent":"NPC Arakawa consented; procedure directly chosen by player"})],
    28: [fact("s7_second_vial", {"status":"new distinct second sealed vial created by Shiki after first was used",
                                "future":"available for other recipients if handed over later"})],
    36: [fact("s7_kadziura_discussion", {"at":"0006-10-03T01:29:00",
         "content":"Shiki provided functional pre-existing Sep 25 S17-A/S17-B assay and explanation of nsp-delta, plus labeled hypothesis of a two-peptide treatment",
         "not_disclosed":"Kuroda's independent donor genotypes and heterokaryon not transmitted to Kadziura"})],
    38: [fact("s7_final_scene", {"at":END,
         "place":"Central Hospital; Kadziura's private office",
         "shiki_action":"handed over SECOND sealed experimental carrier vial and said doctor could handle it",
         "current_custody":"Kadziura stored second vial unopened in protected metal tray in locked private inner cabinet",
         "recipient_knowledge":"Kadziura knows proposed functional mechanism, has NOT been told Byoton origin, Zero Genome or Nemera",
         "human_trial":"Arakawa had partial real trial, but Kadziura was not informed; do not attribute knowledge",
         "no_automatic_action":"no other patient received the carrier; Shiki has not left or declared next action"}),
         fact("s7_clock_status",{"at":END, "lab_registry_check":"completed, no direct report to Shiki",
                                 "arakawa_reinforcement":"completed, assisted exterior and evacuation",
                                 "s17_existing_samples_lookup":"PENDING until 0006-10-03T01:42:00"})],
}

def source_checked_rows(root):
    p=root/SOURCE
    data=p.read_bytes()
    if blob(data) != EXPECTED_SOURCE_BLOB:
        raise ValueError("S7 immutable source differs from reviewed Git blob: " + blob(data))
    rows=[json.loads(line) for line in data.decode("utf-8").splitlines() if line.strip()]
    if len(rows) != 38:
        raise ValueError("Expected 38 accepted live S7 records, got %d" % len(rows))
    for n,row in enumerate(rows,1):
        if row.get("id") != f"s07.text.{n:03}":
            raise ValueError("S7 record gap/duplication at %d: %s" % (n,row.get("id")))
        if row.get("at") < BEGIN or row.get("at") > END:
            raise ValueError("Out-of-session timestamp: " + row["id"])
    if rows[-1]["id"] != LAST or rows[-1]["at"] != END or rows[-1].get("kind") == "ooc":
        raise ValueError("Incorrect terminal turn")
    if "Кадзиур" not in rows[-1]["gm"] or "Бётон" in rows[-1]["gm"]:
        raise ValueError("S7 final scene still contains forbidden NPC disclosure")
    if any(not row.get("gm") or not row.get("place") for row in rows if row.get("kind")!="ooc"):
        raise ValueError("S7 source has an empty played scene")
    return rows,hashlib.sha256(data).hexdigest()

def check_ending(root,rows):
    p=json.loads((root/"recovery/TURN.json").read_text(encoding="utf-8"))
    if p["id"] != LAST or p["at"] != END or p.get("gm") != rows[-1]["gm"]:
        raise ValueError("Terminal TURN differs from source or new turn began")
    current=hot.fold(root)
    if current["errors"] or current["cursor"]!=LAST:
        raise ValueError("S7 HOT not synchronized: " + str(current))
    h=json.loads((root/"recovery/HOT.json").read_text(encoding="utf-8"))
    if h["cursor"]["at"]!=END or h["last"]["gm"] != rows[-1]["gm"]:
        raise ValueError("HOT terminal scene differs from accepted exact last turn")
    return current

def replay(root,rows,source_sha):
    game=Campaign(root/"campaign.sqlite")
    start=game.verify()
    if not (start["session"]==7 and start["revision"]==463 and start["at"]==BEGIN
            and not start["pending"]):
        raise ValueError("Wrong cold checkpoint for S7 recovery: " + str(start))
    if not git_state.verify_manifest(json.loads((root/"state.json").read_text(encoding="utf-8")),game.path):
        raise ValueError("Original checkpoint manifest failed SHA verification")
    memory=root/"recovery/SCENE_MEMORY_INDEX.json"
    idx=json.loads(memory.read_text(encoding="utf-8"))
    index_cursor=idx.get("cursor",{}).get("turn",LAST)
    record=[]
    played=0
    for n,t in enumerate(rows,1):
        ref=f"{SOURCE}#{t['id']}"
        if t.get("kind")=="ooc":
            record.append({"id":t["id"],"status":"ooc","at":t["at"],"source":ref})
            continue
        state=game.verify()
        if t["at"]<state["at"]:
            raise ValueError("Game time regression: "+t["id"])
        with game.connection() as con:
            unresolved = con.execute(
                "SELECT id,due_at FROM processes WHERE status='active' AND due_at < ? ORDER BY due_at,id",
                (t["at"],)).fetchall()
        if unresolved:
            raise ValueError("Cannot replay across unadjudicated persistent NPC processes at " +
                             t["id"] + ": " + str([(v["id"],v["due_at"]) for v in unresolved]))
        with game.connection() as con:
            prev=con.execute("SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1").fetchone()["id"]
        events=[]
        if n in MILESTONES:
            events=[{"id":f"s7.confirmed.{n:03}","at":t["at"],
                     "summary":"Source-checked S7 milestone; see exact played turn " + t["id"],
                     "visibility":"player_known",
                     "source_ref":ref,
                     "operations":MILESTONES[n]}]
        packet={"id":f"s7.played.{n:03}",
                "expected_revision":state["revision"],"at":t["at"],
                "location":t["place"],"player_input":t["player"],"text":t["gm"],
                "input_mode":"direction","events":events,
                "source_ref":ref,"source_status":"exact",
                "memory_gate":{
                    "schema":"npr-memory-gate/1","previous_turn_id":prev,
                    "index_turn":index_cursor,"index_blob_sha":blob(memory.read_bytes()),
                    "player_input":t["player"],
                    "source_refs":[{"source":SOURCE,"sha256":source_sha}],
                    "unretrieved":[],"mode":"stale_index_plus_current_hot",
                }}
        if n==38:
            packet["scene"]={"id":"s8.kadziura.office.0135","actors":["shiki"],
               "beat":"conflict","focus":"Shiki handed experimental second vial to Kadziura; next choice not made"}
        try:
            receipt=game.prepare(packet)
            game.confirm(packet["id"],receipt["draft_sha"])
        except Exception as exc:
            raise RuntimeError("S7 replay failed at " + t["id"]+" (revision "+str(state["revision"])+")") from exc
        played+=1
        record.append({"id":t["id"],"status":"played","sqlite":packet["id"],
                       "at":t["at"],"source":ref})
    if played!=33:
        raise ValueError("Expected 33 played turns; got "+str(played))
    return game,record,played

def post_close_memory(root,closed,revision):
    # HOT is ahead of the old binary and holds the authoritative last scene.
    # Rehydrate before editing, never replace it with a 22:47 SQLite summary.
    full,_=hot._read_state(root)
    if full.get("cursor",{}).get("id")!=LAST:
        raise ValueError("Pre-closure HOT cursor changed")
    off=full.setdefault("offscreen",{})
    clocks=off.setdefault("clocks",{})
    for key in list(clocks):
        clock=clocks[key]
        if isinstance(clock,dict) and clock.get("closed"):
            # Preserve this already played result as a resolved historical entry.
            resolved=off.setdefault("resolved",[])
            if not any(e.get("id")==f"clock:{key}" for e in resolved):
                resolved.append({"id":f"clock:{key}","at":clock.get("due",END),
                    "turn":LAST,"outcome":clock["closed"],
                    "what":clock.get("what","")})
            del clocks[key]
    off["resolved"]=off.get("resolved",[])[-hot.RESOLVED_KEEP:]
    full["_undo"]=None
    # Archive is immutable source for full chronology, HOT stays small.
    full["chronicle"]=full.get("chronicle",[])[-2:]
    full["canon"]=full.get("canon",[])[-3:]
    full.setdefault("lint",{})["recent"]=full.get("lint",{}).get("recent",[])[-2:]
    full["handoff"]={
        "closed":7,"next_session":8,"sqlite_revision":revision,"at":END,
        "last_turn":LAST,"first_turn":"s08.text.001","source":HANDOFF}
    hot.refresh(full)
    hot._write_state(root,full)
    # Derived actor packet must be regenerated after removing completed clocks.
    _,accepted=scene_gate.played_turns(root)
    brief=live_scene.build(full,accepted)
    save(root/live_scene.PATH,brief)
    hot._write_checks(root,full)
    check=hot.check(root)
    if not check["ok"]:
        raise ValueError("Failed post-close HOT and scene-memory audit: "+str(check))
    if any(key in full["offscreen"]["due_now"]
           for key in ("lab_registry_check","arakawa_reinforcement")):
        raise ValueError("Previously finished clocks resurfaced as overdue")
    return check

def main(root:Path):
    rows,source_sha=source_checked_rows(root)
    end=Campaign(root/"campaign.sqlite").verify()
    if end["session"]==8 and end["at"]==END:
        if not (root/INDEX).is_file() or not (root/HANDOFF).is_file():
            raise ValueError("S8 binary exists but evidence index/handoff missing")
        # The binary is already immutable and verified. A later engine
        # enhancement can refresh its derived GM briefing without advancing
        # the game, replaying a turn, or touching HOT's accepted narrative.
        complete,_ = hot._read_state(root)
        if complete.get("cursor",{}).get("id")!=LAST:
            raise ValueError("Live turn moved after S8 acceptance")
        _, accepted = scene_gate.played_turns(root)
        save(root/live_scene.PATH, live_scene.build(complete,accepted))
        result=hot.check(root)
        if not result["ok"]:
            raise ValueError("S8 derived packet refresh broke state: "+str(result))
        print("S7_TO_S8_ALREADY_PUBLISHED_DERIVED_PACKET_REFRESHED",end,flush=True)
        return
    check_ending(root,rows)
    game,index,played=replay(root,rows,source_sha)
    if game.verify()["at"]!=END:
        raise ValueError("SQLite failed to reach the exact final time")
    closed=sessions.close(game,game.verify()["revision"],root/"archive/session-007.md")
    audit=verification.audit(game)
    if audit["session"]!=8 or audit["at"]!=END:
        raise ValueError("Session close did not open S8: "+str(audit))
    with game.connection() as con:
        qty=con.execute("SELECT count(*) FROM journal WHERE session=7 AND kind='turn'").fetchone()[0]
        if qty!=played or not compaction.audit(con,root):
            raise ValueError("Source journal or immutable session compaction failed")
    save(root/INDEX,{
        "format":"npr-shiki-session7-replay/1","source":SOURCE,
        "source_sha256":source_sha,"source_blob":EXPECTED_SOURCE_BLOB,
        "original_user_export_shipped_separately":True,
        "count":len(rows),"played":played,
        "ooc":[row["id"] for row in rows if row.get("kind")=="ooc"],
        "last_turn":LAST,"terminal":END,"sqlite_revision":audit["revision"],
        "archive_sha256":closed["sha256"],"index":index,
        "superseded_source_notes":{
            "s07.text.017":"canonical edited research is in s07.text.019 and subsequent play",
            "s07.text.030":"repeated already-disclosed Arakawa facts were corrected",
            "s07.text.036":"functional S-17 report only; no culture genealogy given to Kadziura",
            "s07.text.037":"amended to remove unauthorized Byoton disclosure"
        }})
    save(root/HANDOFF,{
        "format":"npr-shiki-intersession-handoff/1","closed_session":7,
        "next_session":8,"revision":audit["revision"],"at":END,
        "first_live_turn":"s08.text.001","last_live_turn":LAST,
        "source":INDEX,"player_choice":"unresolved",
        "scene":"Kusagakure Central Hospital, ICU physician Kadziura private office. Second vial now locked with doctor.",
        "boundaries":[
            "Kadziura knows carrier target and a prior biopsy experiment, not Byoton, Zero Genome, Nemera or human trial in Arakawa.",
            "The second experimental vial is in Kadziura's locked inner cabinet; Shiki handed it over and has not taken it back.",
            "Arakawa underwent a player-authorized first in-vivo trial, showed partial improvement; independence from S-17 unproven.",
            "Kadziura ordered retrieval of existing Sep25 functional S-17 assay and four patient charts; due at 01:42 on 03.10.1283.",
            "Lower Hospital, Nemera and unrelated supply plots may not be merged without sources.",
            "Player has not decided Shiki's next action; no invented travel or disclosure."
        ],
        "review_required":{
            "kadziura_prior":[
                "s07.text.033","s07.text.034","s07.text.035","s07.text.036","s07.text.037","s07.text.038"
            ],
            "purpose":"Prevent already-told facts being performed again as revelations; verify exact originals before claims",
            "prior_warning":"CHECKS warnings on .037/.038 for missing earlier review are historical, not a reason to rewrite source or grant new NPC knowledge"
        }
    })
    checked=post_close_memory(root,closed,audit["revision"])
    state={"format":git_state.FORMAT,**game.verify(),"sha256":file_sha256(game.path)}
    save(root/"state.json",state)
    shutil.rmtree(root/"recovery/sqlite",ignore_errors=True)
    git_state.write_transport(root,state)
    if not git_state.verify_manifest(state,game.path):
        raise ValueError("Finished SQLite SHA mismatch")
    print("S7_TO_S8_SUCCESS",json.dumps({
        "sqlite":state,"played":played,"ooc":len(rows)-played,
        "hot":checked,"original_source_sha256":source_sha,
        "archive_sha256":closed["sha256"]
    },ensure_ascii=False),flush=True)

if __name__=="__main__":
    main(Path(sys.argv[1]))
