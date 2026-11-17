"""Source-locked, refusal-safe intersession transition: Shiki session V -> VI.

Full ChatGPT upload, all recorded (HOT v2) game turns and late cancellations
are independent sources. The last published physical scene s05.text.058 was
explicitly withdrawn and is NEVER replayed. The OOC cancellation remains in
the exact archive. Preserve every closed historical source byte-for-byte;
do not rewrite the previous session's canonical SQLite or any old source file.
"""
from __future__ import annotations
import argparse, difflib, hashlib, json, re, shutil, subprocess
from pathlib import Path
from rpg_engine import git_state, sessions, verification, compaction, hot as live_hot, handoff_guard
from rpg_engine.engine import Campaign, file_sha256

RAW_SHA = "8219a7d48c481af632aea61825b0ede0d3715a92c974a39ea02af1f386682230"
RAW_BLOB = "16246b61102a80dc3fd65acf425d371736ed4846"
BASE_SHA = "1c374c252008c0722defdafa6620664859d959c71aad9415b389dea4b2a51050"
START = "0006-10-01T21:33:28"
END = "0006-10-02T10:12:40"
END_PLACE = "Кусагакуре, подземный комплекс УВБ, коридор у северной двери внутреннего поста В-7"
VOID = {"s05.text.058"}
OOC = {"s05.text.015","s05.text.018","s05.text.022","s05.text.058"}
N_TURNS = 58
N_PLAYED = 54
N_RAW_PROMPTS = 70

def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def gitblob(data: bytes) -> str:
    return hashlib.sha1(f"blob {len(data)}\0".encode()+data).hexdigest()

def source_rows(full: str):
    lines = full.splitlines(keepends=True)
    prompts = [i for i,l in enumerate(lines) if l.strip()=="## Prompt:"]
    replies = [i for i,l in enumerate(lines) if l.strip()=="## Response:"]
    if len(lines)!=9328 or len(prompts)!=N_RAW_PROMPTS or len(replies)!=65:
        raise ValueError("uploaded session V exact source structure changed")
    rows = {}
    for index, begin in enumerate(prompts,1):
        end = prompts[index] if index<len(prompts) else len(lines)
        response = next((p for p in replies if begin<p<end),None)
        player = "".join(lines[begin+2:(response if response is not None else end)]).strip()
        gm = "".join(lines[response+2:end]).strip() if response is not None else ""
        rows[index] = {"prompt":index,"player":player,"gm":gm,
                       "source_ref":f"archive/session-005-source.md#L{begin+1}-L{end}",
                       "line_start":begin+1,"line_end":end,
                       "has_response":response is not None}
    if not rows[69]["player"].startswith("Думаешь?") or not rows[70]["player"].startswith("Я тупанул"):
        raise ValueError("unverified late cancellation location in source")
    if "Отменяй эту сцену" not in rows[70]["player"]:
        raise ValueError("missing source cancellation")
    return rows

def norm(s):
    return re.sub(r"\s+"," ",str(s).replace("\\","")).strip().casefold()

def source_mapping(raw, ledger):
    mapping = {}
    for rec in ledger:
        scored = sorted(((difflib.SequenceMatcher(None,norm(rec["player"]),norm(item["player"])).ratio(),n)
                         for n,item in raw.items()),reverse=True)
        score,n = scored[0]
        if score < .72:
            if rec["id"] not in {"s05.text.034","s05.text.035"}:
                raise ValueError(f"source input not corroborated: {rec['id']} best={n}/{score:.2f}")
            mapping[rec["id"]] = {"prompt":None,"score":round(score,3),
                                    "reason":"corrected published player input; exact HOT ledger takes priority"}
        else:
            mapping[rec["id"]] = {"prompt":n,"score":round(score,3)}
    if mapping["s05.text.057"]["prompt"]!=68:
        raise ValueError("last played turn must be user source prompt 68")
    if mapping["s05.text.058"]["prompt"]!=70:
        raise ValueError("last OOC cancellation must be user source prompt 70")
    return mapping

def fact(subject,name,value,private=False):
    return {"op":"set_fact","subject":subject,"name":name,"value":value,
            "visibility":"gm_only" if private else "player_known"}

def memory(key,subject,text,authority="played",private=False):
    return {"op":"remember","key":key,"subject":subject,"text":text,
            "authority":authority,"category":"canon","status":"open",
            "visibility":"gm_only" if private else "player_known"}

def event(i,when,ops,source_ref,private=True):
    return {"id":f"s5.canon.{i:03}","at":when,
            "summary":f"Итог подтверждённого события IV→V и сессии V из s05.text.{i:03}; не пересказ внутри игрового времени.",
            "visibility":"gm_only" if private else "player_known",
            "source_ref":source_ref,"operations":ops}

def due_events(game,n,target,source_ref):
    with game.connection() as con:
        due=list(con.execute(
           "SELECT id,due_at FROM processes WHERE status='active' AND due_at < ? ORDER BY due_at,id",
           (target,)))
    out=[]
    for p in due:
        pid=p["id"]
        if pid=="s5.clinic.rear_unknown":
            ops=[{"op":"finish_process","id":pid,"status":"resolved",
                  "outcome":{"source":"s05.text.002","result":"Угрожавший у задней двери задержан Шики и тяжело повреждён; право и исход допроса не дополнялись вымыслом."}}]
        elif pid=="s5.clinic.front_unknown":
            ops=[{"op":"finish_process","id":pid,"status":"resolved",
                  "outcome":{"source":"s05.text.002","result":"Нори остановила второго злоумышленника; его состояние известно по сыгранному бою, не по замыслу."}}]
        elif pid=="s4.nori_institutional_case":
            ops=[{"op":"finish_process","id":pid,"status":"resolved",
                  "outcome":{"source":"s05.text.045","result":"Нори самостоятельно организовала наблюдение за охраной В-7; к 09:06 выявлены исчезновение караульного и перестановка постов."}}]
        else:
            raise ValueError(f"Unexamined autonomous process {pid} due {p['due_at']} before s5 #{n}")
        out.append({"id":f"s5.due.{n:03}.{pid}","at":p["due_at"],
                    "summary":"Достигнут срок самостоятельного действия персонажа; последствия восстановлены по фактическому тексту сессии V.",
                    "visibility":"gm_only","source_ref":source_ref,"operations":ops})
    return out

def final_ops(hot, ledger):
    ops=[]
    known={
       "nori":"Хаясэ Нори",
       "kusa_head":"Глава Кусагакуре",
       "takagi_ren":"Такаги Рэн",
       "kage_doppelganger":"Неизвестный двойник Кагэ",
       "captive_guard":"Захваченный охранник внутреннего поста"
    }
    for key,name in known.items():
        card=(hot.get("cast") or {}).get(key, {})
        ops.append({"op":"upsert_npc","npc":{"id":key,"name":name,
             "profile":{"role":card.get("role","Наблюдаемая роль в последней сцене"),
                        "limited_knowledge":"Знания и намерения только по фактически виденной сцене; скрытая личность двойника не определена."}}})
    ops.extend([
      fact("shiki","scene_2026-10-02",{
          "at":END,"place":END_PLACE,
          "played_turn":"s05.text.057",
          "hands":"Свободны; Шики ещё не ответил на предложение двойника.",
          "team":"Настоящий Кагэ, Нори, Такаги, восемь охранников у северной запертой двери В-7",
          "visible_enemy":"За открытой узкой смотровой заслонкой двойник с внешностью и голосом Кагэ",
          "hostage":"Живой шиноби с раной брови, у горла короткий клинок; второй пленник внутри отдельно",
          "request":"ключевая печать внутреннего хранилища за живых пленных",
          "barriers":"Стальная дверь и запечатанные заслонки не позволяют пройти шуншином насквозь",
          "void":"Предыдущая попытка Шики прорваться по потолку s05.text.058 отменена полностью."
       },True),
      fact("shiki","forbidden_studies_oct2",{
          "shiton_B":"Кашель Гниющего Лотоса освоен и применён в бою с Сагарой; Сагара умер от поражения лёгких, действие техники прекратилось после смерти",
          "byoton_B":"Плачущие Бубоны освоены и применены к правому глазу пленника; действие остановлено, глаз утрачен",
          "byoton_gisho":"«Ложная рана» передана Немерой как ПРИНЦИП, техника ещё не освоена"
       },True),
      fact("shiki","nemera_sensory_2026-10-02",{
          "from":"0006-10-02T02:09:00",
          "opened":"односторонняя копия зрения, слуха, соматических ощущений и аффективной окраски",
          "closed":"управление мышцами, доступ к произвольным мыслям и воспоминаниям",
          "scope":"Немера получает то, что реально доступно Шики; не знает закадровую правду без канала"
       },True),
      fact("shiki","pact_stage",hot.get("pact",{}).get("stage",3),True),
      fact("shiki","pact_value",hot.get("pact",{}).get("value",0),True),
      fact("sagara","death","1 октября 1283, 21:41, смерть от массивного некротического поражения лёгких после применения Шики Ситона, подтверждена медицинской группой."),
      fact("tetsu","death_report_oct1","1 октября в 08:17 Нори сообщила Шики о смерти Тэцу при переводе от повторного кровотечения. Шики не видел тела или независимого вскрытия; статус остаётся сообщением, а не прямым наблюдением."),
      fact("case_19-T","special_check","1 октября Кагэ распорядился о закрытой приоритетной проверке по цепочке Инаба—корпус №3—С-17—В-7, с участием Тацумори Идзуми и проверкой законности санкции резиденции."),
      fact("case_19-T","patient_count","24 сентября четверо пациентов спасены живыми; по восстановленному реестру их восемь, ещё четыре местонахождения неизвестны."),
      fact("case_19-T","distinct_from_lower_hospital","По прямой поправке игрока Нижний госпиталь, Уцуро, Саю и семь запоров НЕ включаются в расследование В-7 без обоснованной игровой связи."),
      fact("case_19-T","tetsu_conspiracy","Серия атак и подложных документов, спорные квитанции и приказы на В-7, попытки устранения свидетелей. Имена организаторов определяются только доказательствами, доступными NPC."),
      fact("kusa_head","shiki_disclosure","Шики лично показал Кагэ знак семи запоров, рассказал о договоре и назвал Бётон и Шитон; глава не получил автоматически генотип Шики или тайны, о которых не было сказано."),
      fact("seina","shiki_disclosure","Шики рассказал Сэйне и родителям о договоре семи запоров, Немере, Уцуро и изменениях; запретил передавать свои биообразцы третьим лицам."),
      fact("messenger_injured","medical_oct2","Рана печени под правой рёберной дугой, кровотечение и шок; Шики провёл лишь частичный гемостаз. В 10:07 принят хирургами. Полного выздоровления и восполнения кровопотери ещё не было."),
      fact("nori","status_oct2","Нори не восстановлена официально в должности; сопровождает команду Кагэ как знающий план внутреннего поста проводник."),
      fact("kage_doppelganger","observed_state","02.10 в 10:12:40 неизвестный с абсолютно сходными лицом и голосом Кагэ удерживает заложника по ту сторону закрытой стальной двери. Подлинная личность и техника неизвестны.",True),
      memory("s5.void.058","shiki",
             "Последняя первоначальная игровая версия s05.text.058 про потолочный шуншин, снятие заслонки и рану заложника ОТМЕНЕНА игроком. Сцена возвращена к концу s05.text.057; OOC подтверждение отмены s05.text.058 сохранено дословно.",authority="player"),
      memory("s5.clinical_archive_boundary","case_19-T",
             "Нижний госпиталь не связан с делом Инабы/В-7 без новой доказанной причины. Ошибочный первый доклад Кагэ переписан в s05.text.019; старые абзацы не считать каноном.",authority="player"),
      memory("s5.parent_data","shiki",
             "Сэйна хранит только старые обычные клинические анализы Шики до модификаций 1281–1282; все геномные эксперименты после интеграций Хаку/Ходзуки были тайны и проводились в лаборатории Шики.",authority="player",private=True)
    ])
    ops += [
        {"op":"start_process","id":"s6.v7.doppelganger_hostage","owner":"kage_doppelganger",
          "goal":"Использовать живого захваченного охранника для получения ключ-печати внутреннего хранилища, либо защищать себя самостоятельно по собственным реальным возможностям",
          "due_at":"0006-10-02T10:12:44","visibility":"gm_only",
          "notes":"Актуальная дверь ЗАКРЫТА. Цели, физика и решение без вмешательства принадлежат NPC, не исходу отменённой сцены."},
        {"op":"start_process","id":"s6.v7.kage_takagi","owner":"takagi_ren",
          "goal":"Изолировать внутренний пост, вскрыть каналы и спасти заложников, не передав ключ противнику",
          "due_at":"0006-10-02T10:12:46","visibility":"gm_only",
          "notes":"Два специалиста готовы к применению фуин-свитков, один с арбалетом. Пока никто не атаковал, взлома двери не было."}
    ]
    return ops

def main(root:Path):
    raw_file=root/"archive/session-005-source.md"
    raw_bytes=raw_file.read_bytes()
    if sha(raw_bytes)!=RAW_SHA or gitblob(raw_bytes)!=RAW_BLOB:
        raise ValueError("byte-inexact user supplied primary log")
    raw=source_rows(raw_bytes.decode("utf-8"))
    ledger_file=root/"archive/session-005-turns.jsonl"
    log=ledger_file.read_text(encoding="utf-8")
    rows=[json.loads(line) for line in log.splitlines() if line.strip()]
    if len(rows)!=N_TURNS or [x["id"] for x in rows]!=[f"s05.text.{n:03}" for n in range(1,59)]:
        raise ValueError("full HOT v2 source journal not yet folded through OOC cancellation")
    if {x["id"] for x in rows if x.get("kind")=="ooc"}!=OOC:
        raise ValueError("unexpected OOC or missing canonical correction")
    if not rows[-1]["player"].startswith("Я тупанул") or rows[-2]["at"]!=END:
        raise ValueError("session V terminal cancellation/scene not verified")
    if not rows[-2]["gm"].rstrip().endswith("**Ход Шики.**"):
        raise ValueError("last physical scene lacks a published outcome")
    if "А вот с ним" not in rows[-2]["gm"] or "смотров" not in rows[-2]["gm"]:
        raise ValueError("last valid physical scene differs")
    mapping=source_mapping(raw,rows)
    h=json.loads((root/"recovery/HOT.json").read_text(encoding="utf-8"))
    g=json.loads((root/"recovery/GM_ONLY.json").read_text(encoding="utf-8"))
    if (h.get("session")!=5 or h.get("cursor",{}).get("id")!="s05.text.058"
        or h["cursor"].get("at")!=END or h.get("scene",{}).get("as_of")!="s05.text.057"
        or g.get("cursor")!="s05.text.058" or h.get("fold",{}).get("errors")):
        raise ValueError("current end-of-V HOT and GM_ONLY do not match")
    if h.get("last",{}).get("id")!="s05.text.057":
        raise ValueError("cancelled last scene unexpectedly in current HOT")
    if "Немера" not in json.dumps(h.get("scene",{}),ensure_ascii=False):
        raise ValueError("independent Nemera access boundary absent")
    game=Campaign(root/"campaign.sqlite")
    baseline=game.verify()
    manifest=json.loads((root/"state.json").read_text(encoding="utf-8"))
    if baseline["session"]==6 and baseline["at"]==END:
        print("ALREADY_CLOSED_S5",baseline,flush=True); return
    if baseline["session"]!=5 or baseline["revision"]!=366 or baseline["at"]!=START or baseline["pending"]:
        raise ValueError("active SQLite must be the verified S4->S5 baseline")
    if (manifest != {"format":git_state.FORMAT,**baseline,"sha256":file_sha256(game.path)}
        or manifest["sha256"] != BASE_SHA):
        raise ValueError("current SQLite manifest does not match immutable starting blob")
    hot_check=live_hot.check(root)
    if not hot_check["ok"]:
        raise ValueError("live source HOT is inconsistent: "+str(hot_check))
    played=0
    index=[]
    ledger_sha=sha(ledger_file.read_bytes())
    mem_file=root/"recovery/SCENE_MEMORY_INDEX.json"
    mem=mem_file.read_bytes()
    mem_data=json.loads(mem)
    index_turn=mem_data.get("cursor",{}).get("turn")
    if not index_turn:
        raise ValueError("memory gate source cursor missing")
    mem_blob=gitblob(mem)
    for i,row in enumerate(rows,1):
        ref=f"archive/session-005-turns.jsonl#s05.text.{i:03}"
        source=mapping[row["id"]]
        if row["id"] in OOC:
            index.append({"id":row["id"],"status":"ooc_canon_or_amendment",
                          "original_source":source})
            continue
        if i==58:raise ValueError("canceled scene must never be replayed")
        target=row["at"]
        location=row["place"]
        if not isinstance(row.get("gm"),str) or not row["gm"].strip():
            raise ValueError(f"missing exact scene {row['id']}")
        current=game.verify()
        if target<current["at"]:
            raise ValueError(f"non-monotone source clock at {row['id']}")
        events=due_events(game,i,target,ref)
        ops=[]
        if i==11:
            ops.append(fact("sagara","death","Смерть Сагары констатирована медицинской группой в 21:41 после активного воздействия Ситона Шики."))
        if i==19:
            ops.append(memory("s5.lower_hospital_omitted","case_19-T",
               "По прямой поправке игрока удалён сюжетный перенос Нижнего госпиталя в отчёт Кагэ. Итоговый сыгранный доклад только про корпус №3, С-17, Инабу и В-7.",authority="player"))
        if i==34:
            ops.append(memory("s5.genome_chronology_correction","shiki",
               "Последняя обычная клиническая запись Сэйны 18 июля 1279 года; модификации Хаку/Ходзуки произошли позже, в 1281–1282. Тайные генетические материалы никогда не были доступны Сэйне.",authority="player",private=True))
        if i==42:
            ops.append(fact("shiki","nemera_sensory_window_2026-10-02",
                {"opened":"зрение, слух, ощущения, эмоциональная окраска — только в одну сторону",
                 "closed":"моторика, мысли, воспоминания"},True))
        if i==57:
            ops+=final_ops(h,rows)
        if ops:events.append(event(i,target,ops,ref))
        with game.connection() as con:
            prev=con.execute("SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1").fetchone()
        if prev is None:raise ValueError("no predecessor in canonical journal")
        packet={
           "id":f"s5.played.{i:03}",
           "expected_revision":current["revision"],"at":target,
           "location":location,"player_input":row["player"],
           "text":row["gm"],"input_mode":"direction",
           "events":events,"source_ref":ref,"source_status":"exact",
           "memory_gate":{"schema":"npr-memory-gate/1",
              "previous_turn_id":prev["id"],"index_turn":index_turn,
              "index_blob_sha":mem_blob,"player_input":row["player"],
              "source_refs":[{"source":"archive/session-005-turns.jsonl","sha256":ledger_sha},
                             {"source":"archive/session-005-source.md","sha256":RAW_SHA}],
              "unretrieved":[],"mode":"stale_index_plus_current_hot"}
        }
        if i==57:
            packet["scene"]={"id":"s6.uwb.v7.northern_hostage",
                             "actors":["shiki","nori","kusa_head","takagi_ren","kage_doppelganger","captive_guard"],
                             "beat":"conflict",
                             "focus":"Двойник Кагэ с ножом у горла захваченного охранника за узкой смотровой щелью закрытой двери В-7. Свобода реакции Шики сохранена."}
        try:
            rec=game.prepare(packet)
            game.confirm(packet["id"],rec["draft_sha"])
        except Exception as exc:
            raise RuntimeError(f"s5 replay rejected at {i}, revision {current['revision']}, {target}: {events!r}") from exc
        played+=1
        index.append({"id":row["id"],"status":"played","canonical_id":packet["id"],
                      "at":target,"source_ref":ref,"original_source":source})
        if played%12==0:
            print("S5_REPLAY_CONFIRMED",played,"revision",game.verify()["revision"],flush=True)
    if played!=N_PLAYED or game.verify()["at"]!=END:
        raise ValueError(f"replay ended unexpectedly at {played}")
    closed=sessions.close(game,game.verify()["revision"],root/"archive/session-005.md")
    result=verification.audit(game)
    if (result["session"]!=6 or result["revision"]!=421 or result["at"]!=END
        or result["pending"] or result["compacted_sessions"]!=5):
        raise ValueError("S5->S6 closure verification failed: "+str(result))
    for turn in ("s5.played.001","s5.played.034","s5.played.057"):
        if not game.entry("turn",turn,audience="gm")["text"]:
            raise ValueError("exact archived narrative missing: "+turn)
    with game.connection() as con:
        if con.execute("SELECT count(*) FROM journal WHERE session=5 AND kind='turn'").fetchone()[0]!=N_PLAYED:
            raise ValueError("unconfirmed or OOC game turn in canonical SQLite")
        if con.execute("SELECT 1 FROM journal WHERE id='s5.played.058'").fetchone():
            raise ValueError("CANCELED SCENE ENTERED CANON")
        if not compaction.audit(con,root):
            raise ValueError("compacted historical journal does not verify")
        for pid in ("s6.v7.doppelganger_hostage","s6.v7.kage_takagi"):
            if con.execute("SELECT status FROM processes WHERE id=?",(pid,)).fetchone()[0]!="active":
                raise ValueError("new NPC process was not established")
    index_obj={
        "format":"npr-shiki-session5-replay/1","raw_sha256":RAW_SHA,
        "raw_git_blob":RAW_BLOB,"raw_source":"archive/session-005-source.md",
        "live_source":"archive/session-005-turns.jsonl",
        "live_sha256":ledger_sha,"session":5,
        "raw_prompts":N_RAW_PROMPTS,"raw_responses":65,
        "live_turns":N_TURNS,"played_sqlite_turns":played,
        "ooc_entries":sorted(OOC),"canceled_source_prompt":69,
        "cancellation_source_prompt":70,
        "terminal_source_turn":"s05.text.057","terminal_at":END,
        "closed_sqlite_revision":result["revision"],
        "export_sha256":closed["sha256"],
        "index":index,"unmatched_raw":[
           {"source":r["source_ref"],"prompt":n,"status":
            "VOID_AND_RETRACTED" if n==69 else "OOC_CANCELS_VOID" if n==70 else
            "unpublished_or_ooc_or_revised_source_only"} for n,r in raw.items()
            if n not in {x["original_source"]["prompt"] for x in index if x["original_source"].get("prompt") is not None}
           ]
    }
    (root/"archive/session-005-index.json").write_text(
        json.dumps(index_obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    handoff={
        "format":"npr-shiki-intersession-handoff/1",
        "closed_session":5,"next_session":6,"revision":result["revision"],
        "at":END,"world":"Понедельник, 2 октября 1283 года, 10:12:40",
        "place":END_PLACE,"first_live_turn":"s06.text.001",
        "source_turn":"s05.text.057","canceled":"s05.text.058 (all previously published combat consequences are void)",
        "front_of_scene":{
            "shiki":"в составе группы настоящего Кагэ, Нори, Такаги перед запертой стальной дверью; после обращения двойника ничего ещё не предпринял",
            "doppelganger":"за узким открытым смотровым окном, точная видимая и голосовая копия Кагэ; неизвестный, нет доказанного происхождения техники",
            "hostages":"один раненый шиноби у смотрового окна с клинком у горла, второй удерживается за дверью; судьба второго требует источника",
            "door":"тяжёлая сталь, печати, закрыто; шуншин не проходит через неё",
            "own_side":"подлинный Кагэ рядом, Нори проводник, Такаги командует, восемь охранников у входа (двое ранены), двое со свитками, один с арбалетом",
            "demand":"ключевая печать внутреннего хранилища В-7 в обмен на жизнь захваченных; двойник адресовал реплику Шики"
        },
        "recent_results":{
            "clue":"дело Инабы, С-17, корпуса №3 и В-7 находится на закрытой проверке Кагэ; виновники не установлены для игрока",
            "clinical":"раненый посыльный передан хирургам с продолжающимся внутрибрюшным кровотечением и шоком; не объявлять его выздоровевшим",
            "pact":"Шики открыл Немере одностороннюю копию восприятия и ощущений, мысли и моторика под семью замками",
            "techniques":"Две B-техники освоены, теория «Ложной раны» только получена, не освоена",
            "knowledge":"Кагэ лично информирован Шики о признаках семи запоров и названиях Ситона и Бётона, родители/Сэйна знают о договоре, но не тайных исследованиях",
            "retcons":"Нижний госпиталь не связан с В-7 только по тематике; старые ошибочные сцены не поднимать"
        },
        "source_priority":"Прямое позднее исправление пользователя > утверждённая каноническая сцена > подтверждённый точный архив > индекс/вспомогательные резюме",
        "source_index":"archive/session-005-index.json",
        "private_clocks":"game-state/shiki:recovery/GM_ONLY.json, проверить due до перехода времени",
        "hot_policy":"На этой границе SQLite уже session6, HOT остаётся session5 со своим последним OOC id s05.text.058. Первый s06.text.001 открывает живую session6 автоматически.",
        "player_action":"После завершения сессии V произвольных действий Шики нет."
    }
    out=root/"campaigns/shiki/continuity/SESSION_005_TO_006_CANON_1283-10-02.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(handoff,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    # ONLY DERIVED OPERATIONAL DATA ARE PRUNED. Full dialogue is verified in
    # closed SQLite, immutable user upload, source JSONL, timeline and canon archive.
    h["chronicle"]=h.get("chronicle",[])[-2:]
    h["canon"]=h.get("canon",[])[-3:]
    h["chronicle_older"]="archive/session-005-chronicle.md"
    h["canon_older"]="archive/shiki-canon.jsonl"
    h["lint"]={"recent":(h.get("lint") or {}).get("recent",[])[-1:],
               "note":"full historic response quality audit is in the exact archive"}
    h["fold"]["commit"]=subprocess.check_output(["git","-C",str(root),"rev-parse","HEAD"],text=True).strip()
    h["handoff"]={
        "closed":5,"next_session":6,"sqlite_revision":result["revision"],
        "at":END,"last_turn":"s05.text.058","first_turn":"s06.text.001",
        "source":"campaigns/shiki/continuity/SESSION_005_TO_006_CANON_1283-10-02.json"}
    # undo is only for *amending the latest live turn*. Last live session is
    # now immutably closed; all late corrections remain in canon and raw archive.
    # A fresh first s06 turn creates a new safe undo snapshot automatically.
    g["undo"]=None
    (root/"recovery/HOT.json").write_text(
        json.dumps(h,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")
    (root/"recovery/GM_ONLY.json").write_text(
        json.dumps(g,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")
    chk=live_hot.check(root)
    if not chk["ok"]:raise ValueError("final compact HOT invalid: "+str(chk))
    assert (root/"recovery/HOT.json").stat().st_size < 72000
    assert (root/"recovery/GM_ONLY.json").stat().st_size < 30000
    # Canonical archive has to exist *before* old corrections are trimmed.
    canon_archive=root/"archive/shiki-canon.jsonl"
    canon_lines=canon_archive.read_text(encoding="utf-8").splitlines()
    if len(canon_lines)<len(h.get("canon",[])):
        raise ValueError("canon archive incomplete after pruning")
    if not any("s05.text.058" in line for line in canon_lines):
        raise ValueError("cancellation not preserved in archive")
    if len((root/"archive/session-005-chronicle.md").read_text().splitlines())<58:
        raise ValueError("source chronicle incomplete")
    # Publish manifest and transport atomically with checked source archive.
    manifest_out={"format":git_state.FORMAT,**game.verify(),"sha256":file_sha256(game.path)}
    (root/"state.json").write_text(json.dumps(manifest_out,ensure_ascii=False,indent=2)+"\n")
    shutil.rmtree(root/"recovery/sqlite",ignore_errors=True)
    git_state.write_transport(root,manifest_out)
    checked=handoff_guard.audit(game,root,full=True)
    if checked["fallback"]!="awaiting_first_live_turn":
        raise ValueError("verified SQLite-to-HOT boundary not recognized: "+str(checked))
    print("S5_TO_S6_SUCCESS",json.dumps({
      "session":result["session"],"revision":result["revision"],"generation":result["generation"],
      "at":result["at"],"played":played,"source_log":len(rows),"OOC_excluded":len(OOC),
      "compact_sessions":result["compacted_sessions"],"sqlite_sha256":manifest_out["sha256"],
      "archive_sha256":closed["sha256"],"hot_bytes":(root/"recovery/HOT.json").stat().st_size,
      "gm_only_bytes":(root/"recovery/GM_ONLY.json").stat().st_size,
      "handoff":checked["fallback"],"cancelled_void":True},ensure_ascii=False),flush=True)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("state",type=Path)
    main(p.parse_args().state)
