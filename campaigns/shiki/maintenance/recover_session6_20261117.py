"""Close Shiki session 6 from the stored exact live-turn ledger.

Source of played prose: archive/session-006-turns.jsonl after hot-fold.
Never replay an OOC entry or assert an unseen outcome.
"""
import hashlib, json, shutil, subprocess, sys
from pathlib import Path
from rpg_engine import git_state, sessions, verification, compaction, hot
from rpg_engine.engine import Campaign, file_sha256

END="0006-10-02T22:47:00"
SRC="archive/session-006-turns.jsonl"

def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def blob(path):
    data=path.read_bytes()
    return hashlib.sha1(b"blob "+str(len(data)).encode()+b"\0"+data).hexdigest()
def record(k,v,private=False):
    return {"op":"set_fact","subject":"case_19-T","name":k,"value":v,
            "visibility":"gm_only" if private else "player_known"}
def due(game,at,n,ref):
    with game.connection() as con:
        entries=con.execute("SELECT id,due_at FROM processes WHERE status='active' AND due_at < ? ORDER BY due_at,id",(at,)).fetchall()
    out=[]
    for item in entries:
        pid,then=item["id"],item["due_at"]
        if pid not in ("s6.v7.doppelganger_hostage","s6.v7.kage_takagi"):
            raise ValueError("unresolved independent process "+pid+" due "+then)
        if n<=2:
            op={"op":"reschedule_process","id":pid,"due_at":"0006-10-02T10:12:48",
                "notes":"Until the physical clash, the opponent and guards maintain their respective stances."}
        else:
            op={"op":"finish_process","id":pid,"status":"resolved",
                "outcome":{"source":"s06.text.003" if "doppel" in pid else "s06.text.004",
                           "result":"The hostage crisis and breach of the first gate were played on screen; see exact source."}}
        out.append({"id":f"s6.due.{n:03}.{pid}","at":then,"summary":"Scheduled V-7 confrontation resolved against exact played outcome.",
                    "visibility":"gm_only","source_ref":ref,"operations":[op]})
    return out

def main(root):
    folded=hot.fold(root)
    if folded["errors"] or folded["cursor"]!="s06.text.046":
        raise ValueError("Incomplete source history: "+str(folded))
    source=root/SRC
    rows=[json.loads(l) for l in source.read_text().splitlines() if l.strip()]
    if len(rows)!=46 or [x["id"] for x in rows]!=[f"s06.text.{n:03}" for n in range(1,47)]:
        raise ValueError("Missing exact sixth-session turns")
    if rows[-1].get("kind")=="ooc" or rows[-1]["at"]!=END or "Курода" not in rows[-1]["gm"]:
        raise ValueError("Wrong last played scene")
    h=json.loads((root/"recovery/HOT.json").read_text())
    g=json.loads((root/"recovery/GM_ONLY.json").read_text())
    if h["cursor"]["at"]!=END or h["last"]["gm"]!=rows[-1]["gm"]:
        raise ValueError("HOT conflicts with terminal original")
    game=Campaign(root/"campaign.sqlite")
    start=game.verify()
    if start["session"]==7 and start["at"]==END:
        print("ALREADY_PUBLISHED",start);return
    if start["session"]!=6 or start["revision"]!=421 or start["at"]!="0006-10-02T10:12:40":
        raise ValueError("Wrong starting campaign.sqlite "+str(start))
    if not git_state.verify_manifest(json.loads((root/"state.json").read_text()),game.path):
        raise ValueError("Baseline checksum mismatch")
    mem=root/"recovery/SCENE_MEMORY_INDEX.json"
    im=json.loads(mem.read_text())["cursor"]["turn"]
    logsha=digest(source)
    played=0
    index=[]
    for n,t in enumerate(rows,1):
        ref=f"{SRC}#{t['id']}"
        if t.get("kind")=="ooc":
            index.append({"turn":t["id"],"status":"ooc","source":ref})
            continue
        st=game.verify()
        if t["at"]<st["at"] or not t.get("gm") or not t.get("place"):
            raise ValueError("Invalid scene "+t["id"])
        ev=due(game,t["at"],n,ref)
        if n==46:
            final=[
                record("s17_preliminary",{"as_of":END,"two_cell_lines":True,
                    "one_confirmed_heterokaryon":True,"chromosome_integration":"not established",
                    "line_b_inaba_graft":"high identification correspondence",
                    "original_donor":"unknown","culture_manufacture":"unknown"}),
                record("s17_samples",{"v7":"mother culture retained",
                    "hospital":"two small aliquots, working and control; both dispatched"}),
                record("v7_security_report",{"at":END,"source":"private message from Takagi",
                    "detained":2,"attempt":"unauthorized guard rotation",
                    "response":"second inner post and service gallery closed",
                    "reported_observers":"strangers on eastern roofs"}),
                record("shiki_specific_motive",{"status":"unresolved",
                    "evidence":"preexisting directed order and threats",
                    "rule":"do not invent sponsor, intent or link with Lower Hospital"},True),
            ]
            ev.append({"id":"s6.final.046","at":t["at"],
                "summary":"Signed S-17 laboratory conclusion and Takagi report at 22:47.",
                "visibility":"player_known","source_ref":ref,"operations":final})
        with game.connection() as con:
            prev=con.execute("SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1").fetchone()["id"]
        packet={"id":f"s6.played.{n:03}","expected_revision":st["revision"],
                "at":t["at"],"location":t["place"],"player_input":t.get("player",""),
                "text":t["gm"],"input_mode":"direction","events":ev,
                "source_ref":ref,"source_status":"exact",
                "memory_gate":{"schema":"npr-memory-gate/1","previous_turn_id":prev,
                    "index_turn":im,"index_blob_sha":blob(mem),
                    "player_input":t.get("player",""),"source_refs":[{"source":SRC,"sha256":logsha}],
                    "unretrieved":[],"mode":"stale_index_plus_current_hot"}}
        if n==46:
            packet["scene"]={"id":"s7.kuroda.lab.2247","actors":["shiki"],
              "beat":"conflict","focus":"Shiki has a signed report and private Takagi message. His reply is undecided."}
        try:
            receipt=game.prepare(packet)
            game.confirm(packet["id"],receipt["draft_sha"])
        except Exception as err:
            raise RuntimeError(f"Replay failed at {t['id']} revision {st['revision']}") from err
        played+=1
        index.append({"turn":t["id"],"status":"played","sqlite":packet["id"],"at":t["at"],"source":ref})
    if game.verify()["at"]!=END:raise ValueError("Game did not reach 22:47")
    closed=sessions.close(game,game.verify()["revision"],root/"archive/session-006.md")
    audit=verification.audit(game)
    if audit["session"]!=7 or audit["at"]!=END:raise ValueError("Unexpected close-session result "+str(audit))
    with game.connection() as con:
        n=con.execute("SELECT count(*) FROM journal WHERE session=6 AND kind='turn'").fetchone()[0]
        if n!=played or not compaction.audit(con,root):
            raise ValueError("SQLite source count or compaction invalid")
    index_path=root/"archive/session-006-index.json"
    index_path.write_text(json.dumps({"format":"npr-shiki-session6-replay/1",
        "source":SRC,"source_sha256":logsha,"source_original_export_in_repo":False,
        "original_export":"User's local attached full source; separate source ZIP provided",
        "played":played,"ooc":[x["id"] for x in rows if x.get("kind")=="ooc"],
        "terminal":END,"sqlite_revision":audit["revision"],
        "archive_sha256":closed["sha256"],"index":index},ensure_ascii=False,indent=2)+"\n")
    handoff={"format":"npr-shiki-intersession-handoff/1","closed_session":6,"next_session":7,
       "revision":audit["revision"],"at":END,"first_live_turn":"s07.text.001",
       "source":"archive/session-006-index.json","player_choice":"unresolved",
       "scene":"Kusagakure Central Hospital, Kuroda lab. Signed S-17 evidence; Takagi private warning.",
       "essential_boundaries":["Neither primary line identified as Shiki genotype",
          "No established relation to Lower Hospital",
          "Personal targeting of Shiki remains unexplained",
          "Takagi and Kuroda know only what their separate sources convey"]}
    path=root/"campaigns/shiki/continuity/SESSION_006_TO_007_HANDOFF_1283-10-02.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(handoff,ensure_ascii=False,indent=2)+"\n")
    h["chronicle"]=h.get("chronicle",[])[-2:]
    h["canon"]=h.get("canon",[])[-3:]
    h["lint"]["recent"]=h.get("lint",{}).get("recent",[])[-1:]
    h["handoff"]={"closed":6,"next_session":7,"sqlite_revision":audit["revision"],
                  "at":END,"last_turn":"s06.text.046","first_turn":"s07.text.001",
                  "source":"campaigns/shiki/continuity/SESSION_006_TO_007_HANDOFF_1283-10-02.json"}
    g["undo"]=None
    # Background aggregate roles are not distinct NPC identities.
    h["scene"]["present"]=[k for k in h["scene"].get("present",[])
                            if k not in ("two_medical_witnesses","junior_lab_technician")]
    for key,value in (("recovery/HOT.json",h),("recovery/GM_ONLY.json",g)):
        (root/key).write_text(json.dumps(value,ensure_ascii=False,indent=1)+"\n")
    chk=hot.check(root)
    if not chk["ok"]:raise ValueError("Post-close HOT review failed "+str(chk))
    if (root/"recovery/GM_ONLY.json").stat().st_size>=30000:
        raise ValueError("GM_ONLY still oversized; preserve hidden context")
    state={"format":git_state.FORMAT,**game.verify(),"sha256":file_sha256(game.path)}
    (root/"state.json").write_text(json.dumps(state,ensure_ascii=False,indent=2)+"\n")
    shutil.rmtree(root/"recovery/sqlite",ignore_errors=True)
    git_state.write_transport(root,state)
    if not git_state.verify_manifest(state,game.path):
        raise ValueError("SQLite checksum not verified")
    print("S6_TO_S7_SUCCESS",json.dumps({"sqlite":state,"played":played,
         "hot":chk,"gm_bytes":(root/"recovery/GM_ONLY.json").stat().st_size,
         "archive_sha256":closed["sha256"]},ensure_ascii=False),flush=True)

if __name__=="__main__":main(Path(sys.argv[1]))
