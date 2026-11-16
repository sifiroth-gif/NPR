"""Close Shiki session IV from the exact primary sources, preserving all retcons.

The already confirmed source is HOT v2's session-004-turns.jsonl. One entire
played scene (.022) is missing from that ledger; extract it from the
byte-verified original chat export. Never rewrite that older ledger, and never
replay canceled OOC or illustration prompts as new in-world player choices.
This command runs in a detached GitHub Actions worktree, not against live play.
"""
import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from rpg_engine import git_state, sessions, verification
from rpg_engine.engine import Campaign, file_sha256
from rpg_engine import compaction

RAW_SHA = "cf038865aef05f487d9577a0ed2b4e36e2511b0bc1d065ca2171f2fc45ba3b21"
RAW_BLOB = "6f69b5c55a1c1f2efaa8e0e539895a8e692c13d0"
BASE_SHA = "fefcc012b0e18085baf9f75c0a9ee3d035b489e65f24a3cc82d95c53cf29310d"
END = "0006-10-01T21:33:28"
START = "0006-09-27T22:05:34"
END_PLACE = "Кусагакуре, кабинет Кирисавы Сэйны, открытая задняя дверь"
OOC = {49, 55, 56, 57, 60, 61, 62}
MAPPING = {
    **{i:i for i in range(1,5)},
    **{i:i-5 for i in range(10,30)},
    34:25,35:26,
    **{i:i-14 for i in range(41,63)},
    64:49,
    **{i:i-15 for i in range(65,70)},
    **{i:i-15 for i in range(70,73)},
    73:58,74:59,75:60,76:61,77:62,
    **{i:i-15 for i in range(78,84)}
}
# Source prompt #40 supersedes published scene .026; #72 supersedes .054.
CORRECTIONS = {
    26: {"raw_prompt":40,"reason":"Player explicitly corrected erroneous homecoming / summons anchor"},
    54: {"file":"campaigns/shiki/continuity/REVISED_MASTERY_SCENE_S04_054_2026_11_16.md",
         "raw_prompt":72,"reason":"Player approved B-tier one-handed forbidden techniques and nullified nonexistent hospital radio calls"},
}
FORBIDDEN_PHONE = ("Минасэ Рэи, 19:51", "В 20:21 коммуникатор", "коммуникатор ожил")

def source_rows(raw):
    lines=raw.splitlines(keepends=True)
    marks=[i for i,v in enumerate(lines) if v.strip()=="## Prompt:"]
    replies=[i for i,v in enumerate(lines) if v.strip()=="## Response:"]
    if len(lines)!=7544 or len(marks)!=83 or len(replies)!=83:
        raise ValueError("raw session four source boundaries disagree")
    result={}
    for n,p in enumerate(marks,1):
        end=marks[n] if n<len(marks) else len(lines)
        r=next((j for j in replies if p<j<end),None)
        if r is None: raise ValueError("unexpected unmatched response")
        result[n]={"n":n,"start":p+1,"end":end,
                   "player":"".join(lines[p+2:r]).strip(),
                   "gm":"".join(lines[r+2:end]).strip(),
                   "source_ref":f"archive/session-004-source.md#L{p+1}-L{end}"}
    return result

def cut_scene(text,day="Суббота"):
    head=re.search(rf"\*\*{day},\s*[^\n]{{3,110}}\*\*",text)
    if not head:raise ValueError(f"source lacks exact in-world heading: {day}")
    end=text.find("**Ход Шики.**",head.start())
    if end<0: raise ValueError("source lacks final player action boundary")
    return text[head.start():end+len("**Ход Шики.**")].strip()

def npc_if_absent(game,n,name,profile):
    with game.connection() as con:
        if con.execute("SELECT 1 FROM npcs WHERE id=?",(n,)).fetchone():return []
    return [{"op":"upsert_npc","npc":{"id":n,"name":name,"profile":profile}}]

def fact(subject,key,value,private=False):
    return {"op":"set_fact","subject":subject,"name":key,"value":value,
            "visibility":"gm_only" if private else "player_known"}

def memo(key,subject,text,private=False,authority="played",category="canon"):
    return {"op":"remember","key":key,"subject":subject,"text":text,
            "authority":authority,"category":category,"status":"open",
            "visibility":"gm_only" if private else "player_known"}

def belief(actor,topic,content,evidence="witnessed",certainty="certain",private=True):
    return {"op":"set_belief","actor":actor,"topic":topic,"belief":content,
            "evidence":evidence,"certainty":certainty,
            "visibility":"gm_only" if private else "player_known"}

def ops_for(n,game):
    ops=[]
    if n==2:
        ops += [
            fact("sayu","ward_attack","Атакующая каменная форма рассечена Шики чакровым скальпелем и обездвижена; поддерживающий контур остался активен, двигательная тяга прекратилась."),
            {"op":"finish_process","id":"s4.utsuro_stone_ward","status":"resolved",
             "outcome":{"source":"s4.played.002","result":"Физический выход каменной твари подавлен, Саю жива; источник в стене остаётся."}}
        ]
    if n==3:
        ops += [
            fact("sayu","biopsy_consent","Разрешила только один поверхностный кожный срез с правой руки и ознакомление с результатами; это не согласие на другие операции."),
            fact("stone_remains","samples","Исследованы живая ткань, соединительные тяжи, участки каменного каркаса. Не доказано общее происхождение тканей с Саю."),
        ]
    if n==4:
        ops.append(memo("s4.correction_lab_boundary","shiki",
            "После операции игрок прямо отверг автоматическое перемещение Шики в обычную общую лабораторию; последующие генетические исследования относятся к его собственному засекреченному оборудованию и действиям.",
            authority="player",private=True))
    if n==8:
        ops.append(fact("shiki","genome_contact","Работа с чужеродным тканевым материалом через Нулевой Геном и строгий фильтр внешнего управления. По игроку клеточная ассимиляция давно доступна и лаборатория не единственный способ.",True))
    if n==12:
        ops += [
            fact("shiki","genome_1283","Три генетические линии после собственного эксперимента: исходный Нулевой Геном, освоенный Хьётон/ограниченная гидратация, и адаптация ткани каменного существа с отказом во входящих чужих командах.",True),
            memo("s4.third_genome","shiki",
                "Шики по своему решению закрепил часть генетической программы из каменного существа с изоляцией внешнего управления; не давать ей самовольно новых способностей и не заявлять полного знания происхождения.",private=True,authority="player")
        ]
    if n==16:
        ops.append(fact("sayu","medical","Шики провёл исцеление Саю в рамках своей развитой медицинской чакры; самостоятельная воля и история пациентки сохраняются."))
    if n==22:
        ops += [
            npc_if_absent(game,"minase","Минасэ Рэи",{"role":"полукаменная хирург Нижнего госпиталя","wants":"сохранить контроль над операционной и не потерять живых пациентов",
                       "knowledge_limits":"видела перенос семи колец Шики, не знает всех способностей Шики"})[0] if npc_if_absent(game,"minase","Минасэ Рэи",{"role":"полукаменная хирург Нижнего госпиталя"}) else None,
        ]
        ops=[op for op in ops if op]
        ops += [
            npc_if_absent(game,"nemera","Немера",{"role":"нечеловеческое разумное существо за границей семи печатей",
                     "knowledge_limits":"не знает мир за запечатанными чувствами, пока не получит доступ",
                     "agency":"собственные цели, не лояльна Шики автоматически"})[0]
            if npc_if_absent(game,"nemera","Немера",{"role":"заключённая"}) else None,
        ]
        ops=[op for op in ops if op]
        ops += [
            fact("shiki","seven_seals_custody","30 сентября 1283 в 19:21 Шики по прямой заявке перенёс семь раздельных функций замков на своё тело; древнее существо заключено внутри, исходный узел пуст.",True),
            fact("shiki","seven_seals_layout",{
                "right_forearm":"пространственный","sternum_lower_ribs":"питающий",
                "shoulders_supraclavicular":"двигательный",
                "neck_jaw":"отпечатковый","left_arm_flank":"патологический",
                "lower_abdomen_lumbar":"ограничитель давления",
                "central_axis":"граница/седьмой замок, внутрь"},True),
            fact("nemera","captivity","Запечатана внутри системы семи независимых контуров Шики; её чужое сознание не получает автоматически контроль над его движением или восприятием.",True),
            memo("s4.seven_locks","shiki",
                 "Источник первичного переноса семи замков — ровно третий акт третьего дня: archive/session-004-source.md#L2597-L2800; игровой ID s4.played.022 отсутствовал в архиве HOT v2, восстановлен из полного оригинала. НЕ забывать случившееся.",private=True,authority="player")
        ]
    if n==23:
        ops.append(fact("shiki","seven_seals_reinforced","После переноса сепарированы управляющие контуры и границы для недопуска внешнего управления; добровольные действия Шики не воспроизводить повторно.",True))
    if n==26:
        ops += [
            fact("lower_hospital","physical_space","Пространство Нижнего госпиталя полуфизично и не тождественно клетке удерживаемого существа; стены остались."),
            fact("shiki","return_1283-09-30","После отзыва обратного призыва 30 сентября 20:07 Шики вернулся к исходному якорю тайной лаборатории, затем скрытно добрался до дома Кусагакуре к 20:28. Не мгновенная телепортация прямо домой.",True),
            memo("s4.return_retcon","shiki",
                 "Окончательная опубликованная по прямой поправке игрока сцена #40 заменяет старый вариант дома/телепортации из #35. Не ошибаться в точке якоря: тайная лаборатория северо-западнее Кусы.",authority="player")
        ]
    if n==40:
        ops += [
            fact("nemera","self_name","Существо само выбрало имя Немера, Шики не присваивал его."),
            memo("s4.nemera_beliefs","nemera","Немера никогда не была человеком; человеческие сравнения — перевод/метафоры. Не приписывать Шики ужас, которого игрок не заявил.",private=True,authority="player")
        ]
    if n==42:
        ops += [
            npc_if_absent(game,"tetsu","Тэцу",{"role":"тяжело раненный возможный свидетель по В-7","status":"жив после помощи Шики; исход дальнейших действий не доказан"})[0] if npc_if_absent(game,"tetsu","Тэцу",{"role":"свидетель"}) else None,
            fact("tetsu","medical","1 октября утром Шики оказал тяжелораненому Тэцу экстренную помощь с хирургией и медицинской чакрой. Пациент жив, дальнейший исход и охрана зависят от фактических событий.")
        ]
        ops=[op for op in ops if op]
    if n==46:
        ops.append(fact("tetsu","custody","1 октября утром Тэцу согласованно передан представителям безопасности; Шики ранее сообщил Нори как свидетель. Не объявлять смерть Тэцу каноном только из угроз неизвестных."))
    if n==54:
        ops += [
            fact("shiki","forbidden_arts_mastery",{
                "shiton":{"name":"Кашель Гниющего Лотоса","rank":"B","delivery":"споры при выдохе","seals":"одноручные левой руки","trained":True},
                "byoton":{"name":"Плачущие Бубоны","rank":"B","delivery":"касание или сенбон правой руки","seals":"одноручные левой руки","infectious_pus":True,"fixed_radius":False,"trained":True},
                "old_c_arts_learned":False},True),
            fact("shiki","pact_stage_recorded",3,True),
            memo("s4.mastery_approved","shiki",
                 "Канон после прямой замены: 1 октября 1283 в 19:52 освоены ДВЕ B-техники, оригинальные печати одной левой руки; три воспроизводимых успешных применения каждой, без дополнительных невыученных C. Отсутствовал звонок Минасэ из Нижнего госпиталя в лабораторию.",private=True,authority="player")
        ]
    if n==58:
        ops.append(memo("s4.false_phone_communication","lower_hospital",
                        "По позднему прямому исправлению игрока сообщения/звонка/доклада от Минасэ через коммуникатор в тайную лабораторию НЕ БЫЛО. Уцуро не может ссылаться на прослушанный вызов.",authority="player"))
    if n==60:
        ops.append(memo("s4.out_of_scope_phone","lower_hospital",
                        "Отменён вымышленный звонок Минасэ и причинно зависевшие от него сведения (включая реакцию Уцуро). Нижний госпиталь в этой сцене не физическая телефонная станция.",authority="player"))
    if n==63:
        ops.append(fact("nori","latest_known_status","1 октября 21:02 по рации сказала Шики: доступ к расследованию и звание под угрозой, вскрытие Тэцу закрыто, местонахождение переданного В-7 ей неизвестно. Это её слова, не всеведущий факт о судьбе Тэцу."))
    if n==65:
        ops += [
            npc_if_absent(game,"seina","Кирисава Сэйна",{"role":"наставница Шики, опытная медицинская тюнин; владелица частного кабинета",
                        "knowledge_limits":"видит внешность Шики и его действия здесь, не знает тайных генетических экспериментов"})[0]
            if npc_if_absent(game,"seina","Кирисава Сэйна",{"role":"наставница"}) else None,
            npc_if_absent(game,"akihiro","Мугами Акихиро",{"role":"отец Шики, сотрудник кабинета Сэйны",
                        "knowledge_limits":"видит сыновнюю внешность и опасность, не знает Немеру или Ситон"})[0]
            if npc_if_absent(game,"akihiro","Мугами Акихиро",{"role":"отец"}) else None,
            npc_if_absent(game,"nao","Мугами Нао",{"role":"мать Шики, сотрудница кабинета Сэйны",
                        "knowledge_limits":"видит изменения тела и обстановку, не знает тайной исследовательской биологии"})[0]
            if npc_if_absent(game,"nao","Мугами Нао",{"role":"мать"}) else None,
            fact("shiki","family_clinic","Кабинет наставницы Кирисавы Сэйны в Кусагакуре; там работают оба родителя Шики: Акихиро и Нао."),
        ]
        ops=[op for op in ops if op]
    if n==66:
        ops += [
            fact("clinic.seina","shelter","По просьбе Шики Акихиро, Нао и Сэйна укрылись вместе с пациентами за закрытой изнутри дверью третьей палаты."),
        ]
    if n==68:
        ops += [
            npc_if_absent(game,"rear_unknown","Неизвестный с перевязанной левой кистью",
                {"role":"неизвестный у задней двери кабинета Сэйны","visible_goal":"потребовать прекратить дело Тэцу/В-7, запугать Шики",
                 "knowledge_limits":"знает фамилию Шики и узнаёт его лицо только при встрече; исполнитель/заказчик не подтверждены"})[0] if npc_if_absent(game,"rear_unknown","Неизвестный",{}) else None,
            npc_if_absent(game,"front_unknown","Неизвестный у фасада",
                {"role":"подозрительный наблюдатель перед клиникой, остановлен Нори",
                 "knowledge_limits":"распознал капитана Нори; подчинённость второму пока не доказана"})[0] if npc_if_absent(game,"front_unknown","Неизвестный",{}) else None,
            fact("shiki","scene",{"at":END,"location":END_PLACE,"hands":"обе на виду","rear_door":"открыта",
                "rear_unknown":"снаружи, перевязанная левая кисть, прямой шантаж семьи и В-7/Тэцу; не атаковал",
                "front_unknown":"у фасада с Нори, не показал отправителя",
                "nori":"снаружи у переднего входа, не видела Шики после возвращения",
                "family":"Акихиро, Нао, Сэйна и больные за запертой палатой",
                "enemies":"неизвестны мотивы и заказчик; непосредственные угрозы произнесены"},True),
            fact("shiki","pact_presence","Стадия 3: основной эффект при осознанной близости — тихое предчувствие долгой мучительной смерти; слабый реальный вторичный запах старой затхлой больницы, сырой земли и тления. Нет шлейфа/вреда скрытности; нет фоновой инфекции, пока не наступит стадия 5 и кожный контакт.",True),
            memo("s4.smell_final","shiki",
                 "Поздняя прямая поправка игрока в s04.text.068 возвращает реальный едва заметный запах при близком контакте; ранее #76 и #77 сделали его второстепенным к тревожному предчувствию; запах не раскрывает скрытого Шики.",authority="player",private=True),
            belief("rear_unknown","shiki.encounter","Лично увидел нового истощённого Шики, уловил вторичный запах, испытал необъяснимую угрозу; знает его фамилию и дом семьи."),
            belief("front_unknown","nori.encounter","Лично назвал Нори капитаном, ведёт с ней разговор у фасада; неизвестно, кто отправитель."),
            {"op":"start_process","id":"s5.clinic.rear_unknown","owner":"rear_unknown",
             "goal":"Добиться прекращения дела В-7/Тэцу угрозой семье, сохранив возможность отступить или применить силу",
             "due_at":"0006-10-01T21:33:31","visibility":"gm_only",
             "notes":"Шики открыл служебную дверь, обе руки на виду; человек снаружи. Его следующее действие не предрешено."},
            {"op":"start_process","id":"s5.clinic.front_unknown","owner":"front_unknown",
             "goal":"Связать Нори угрозами и добиться её отступления от расследования",
             "due_at":"0006-10-01T21:33:32","visibility":"gm_only",
             "notes":"Нори требует имя направившего; неизвестный не ответил. Не приписывать автоматически союз с человеком у задней двери."}
        ]
        ops=[op for op in ops if op]
    return ops

def main(root):
    original=root/"archive/session-004-source.md"
    raw=original.read_text(encoding="utf-8")
    if hashlib.sha256(raw.encode()).hexdigest()!=RAW_SHA:
        raise ValueError("full raw session upload does not match immutable SHA-256")
    b=raw.encode()
    if hashlib.sha1(("blob "+str(len(b))+"\0").encode()+b).hexdigest()!=RAW_BLOB:
        raise ValueError("Git source blob SHA does not match")
    sources=source_rows(raw)
    if len(MAPPING)!=68 or sorted(MAPPING.values())!=list(range(1,69)):
        raise ValueError("raw-source to HOT id mapping not complete")
    reverse={id_:num for num,id_ in MAPPING.items()}
    f=root/"archive/session-004-turns.jsonl"
    archived=[json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    indexed={int(row["id"].rsplit(".",1)[1]):row for row in archived}
    if len(archived)!=67 or sorted(indexed)!=[i for i in range(1,69) if i!=22]:
        raise ValueError("unexpected source ledger; .022 must be the only missing entry")
    memory_file=root/"recovery/SCENE_MEMORY_INDEX.json"
    memory_bytes=memory_file.read_bytes()
    memory_data=json.loads(memory_bytes)
    if memory_data.get("cursor",{}).get("turn")!="s04.text.048":
        raise ValueError("historical memory index anchor changed")
    memory_blob=hashlib.sha1(
        ("blob "+str(len(memory_bytes))+"\\0").encode().replace(b"\\0",b"\\x00")+memory_bytes
    ).hexdigest()
    ledger_sha=hashlib.sha256(f.read_bytes()).hexdigest()
    # Do not edit the historical ledger. All newly recovered data are kept
    # in independently addressable named artifacts and verified SQLite.
    missing=sources[27]
    recovered_022={
        "format":"npr-recovered-missing-played-turn/1","id":"s04.text.022",
        "at":"0006-09-30T19:21:00","place":"Нулевая операционная. Нижний госпиталь.",
        "player":missing["player"],"gm":cut_scene(missing["gm"]),
        "source":missing["source_ref"],"status":"played_excluded_from_prior_hot_v2_ledger"
    }
    game=Campaign(root/"campaign.sqlite")
    verified=game.verify()
    manifest=json.loads((root/"state.json").read_text())
    if verified["session"]==5 and verified["at"]==END:
        print("ALREADY_COMPLETED",verified,flush=True);return
    if verified["session"]!=4 or verified["revision"]!=304 or verified["at"]!=START or verified["pending"]:
        raise ValueError("current SQLite is not the exact verified S3->S4 baseline")
    if manifest!={"format":git_state.FORMAT,**verified,"sha256":file_sha256(game.path)} or manifest["sha256"]!=BASE_SHA:
        raise ValueError("manifest or SQLite SHA mismatch: refusing to modify campaign")
    hot=json.loads((root/"recovery/HOT.json").read_text())
    private=json.loads((root/"recovery/GM_ONLY.json").read_text())
    if (hot.get("session")!=4 or hot.get("cursor",{}).get("id")!="s04.text.068"
            or hot["cursor"]["at"]!=END or private.get("cursor")!="s04.text.068"
            or hot.get("fold",{}).get("errors")):
        raise ValueError("HOT v2 and GM-only source are not on the same terminal scene")
    if int(hot.get("pact",{}).get("stage",-1))!=3:
        raise ValueError("HOT pact stage changed: re-evaluate source before replay")
    reviewed_path=root/"campaigns/shiki/continuity/REVISED_MASTERY_SCENE_S04_054_2026_11_16.md"
    revised=cut_scene(reviewed_path.read_text(encoding="utf-8"),day="Воскресенье")
    if ("Кашель Гниющего Лотоса" not in revised or "Плачущие Бубоны" not in revised
            or "Никакого звонка" in revised or "**Ход Шики.**" not in revised):
        raise ValueError("missing approved revised forbidden technique scene")
    amended_return=cut_scene(sources[40]["gm"],day="Суббота")
    if ("Тайная лаборатория" not in amended_return or "20:28" not in amended_return):
        raise ValueError("missing corrected original lab return route")
    game.checkpoint(root/"recovery-before-s4.sqlite")
    stream=[];canon_ids=[];at=verified["at"]
    index=[]
    for source_num in range(1,84):
        item=sources[source_num]
        if source_num not in MAPPING:
            status="ooc_or_asset"
            if source_num in {40,72}:status="late_canonical_replacement"
            if source_num in {5,6,7,8,9,36,37,38,39,70,71,75,76,77}:
                status="ooc_canon_amendment"
            index.append({"prompt":source_num,"source_ref":item["source_ref"],"status":status})
            continue
        n=MAPPING[source_num]
        row=recovered_022 if n==22 else indexed[n]
        if n in OOC:
            index.append({"prompt":source_num,"id":row["id"],"status":"ooc_not_replayed",
                          "source_ref":item["source_ref"]})
            continue
        if not row.get("gm") or not row.get("player"):
            raise ValueError(f"missing exact published scene #{n}")
        narrative=row["gm"]
        target_at=row["at"]
        target_place=row["place"]
        text_source=f"archive/session-004-turns.jsonl#s04.text.{n:03}"
        if n==22:text_source=missing["source_ref"]
        if n==26:
            narrative=amended_return
            target_at="0006-09-30T20:28:00"
            target_place="Кусагакуре. Дом Мугами Шики"
            text_source=sources[40]["source_ref"]
        if n==54:
            narrative=revised
            text_source=str(reviewed_path.relative_to(root))+"; "+sources[72]["source_ref"]
        if target_at<at:
            raise ValueError(f"source time regression {n}: {target_at} after {at}")
        events=[]
        with game.connection() as con:
            due=list(con.execute(
                "SELECT id,due_at FROM processes WHERE status='active' AND due_at < ? ORDER BY due_at,id",
                (target_at,)))
        for event in due:
            pid=event["id"]
            if pid=="s4.utsuro_stone_ward" and n==1:
                next_due="0006-09-27T22:05:41"
            elif pid=="s4.nori_institutional_case":
                next_due="0006-10-02T08:00:00"
            else:
                raise ValueError(f"unreviewed NPC process {pid} due {event['due_at']} at source s4 #{n}")
            events.append({"id":f"s4.due.{n:03}.{pid}","at":event["due_at"],
                           "summary":"Срок собственного действия NPC достигнут; источник и неизвестный исход сохраняются; решение не придумано задним числом.",
                           "visibility":"gm_only","source_ref":text_source,
                           "operations":[{"op":"reschedule_process","id":pid,"due_at":next_due,
                                          "notes":f"Следующая проверка по сыгранному ходу s4.played.{n:03}; фактический исход не придуман"}]})
        ops=ops_for(n,game)
        if ops:
            events.append({"id":f"s4.canon.{n:03}","at":target_at,
                "summary":f"Проверенные последствия сыгранного хода сессии IV, источник {row['id']}.",
                "visibility":"gm_only" if n in {8,12,22,23,40,54,68} else "player_known",
                "source_ref":text_source,"operations":ops})
        packet={"id":f"s4.played.{n:03}","expected_revision":verified["revision"],
                "at":target_at,"location":target_place,
                "player_input":row["player"],"text":narrative,"input_mode":"direction",
                "source_ref":text_source,"source_status":"exact",
                "events":events}
        with game.connection() as con:
            previous=con.execute("SELECT id FROM journal WHERE kind='turn' ORDER BY seq DESC LIMIT 1").fetchone()
        if previous is None:
            raise ValueError("cannot substantiate previous played turn for memory gate")
        evidence=[{"source":"archive/session-004-source.md","sha256":RAW_SHA},
                  {"source":"archive/session-004-turns.jsonl","sha256":ledger_sha}]
        if n==54:
            evidence.append({"source":str(reviewed_path.relative_to(root)),
                             "sha256":hashlib.sha256(reviewed_path.read_bytes()).hexdigest()})
        packet["memory_gate"]={
            "schema":"npr-memory-gate/1","previous_turn_id":previous["id"],
            "index_turn":"s04.text.048","index_blob_sha":memory_blob,
            "player_input":packet["player_input"],"source_refs":evidence,
            "unretrieved":[],"mode":"stale_index_plus_current_hot"}
        if n==68:
            packet["scene"]={"id":"s5.seina.clinic.entry","actors":["shiki","rear_unknown"],
                "beat":"conflict","focus":"Обнажённые руки Шики у открытой задней двери; неизвестный шантажирует семью; Нори спорит с другим у фасада."}
        try:
            receipt=game.prepare(packet)
            game.confirm(packet["id"],receipt["draft_sha"])
        except Exception as exc:
            raise RuntimeError(f"FAILED s4 source {source_num} id {n} time {target_at} revision {verified['revision']} events {events}") from exc
        verified["revision"]+=1;at=target_at
        canon_ids.append(packet["id"])
        index.append({"prompt":source_num,"id":row["id"],"canonical_id":packet["id"],
                      "status":"source_recovered" if n==22 else
                      "source_replaced" if n in CORRECTIONS else "played",
                      "at":target_at,"source_ref":item["source_ref"],
                      "canonical_text_source":text_source})
        if len(canon_ids)%16==0:print("CONFIRMED",len(canon_ids),"at",at,"revision",verified["revision"],flush=True)
    if at!=END or len(canon_ids)!=61 or canon_ids[-1]!="s4.played.068":
        raise ValueError("incorrect canonical terminal replay")
    closed=sessions.close(game,verified["revision"],root/"archive/session-004.md")
    final=verification.audit(game)
    if (final["session"]!=5 or final["at"]!=END or final["pending"]
            or final["compacted_sessions"]!=4):
        raise ValueError("session IV not successfully closed and compacted")
    if "Теперь… все семь в тебе" not in game.entry("turn","s4.played.022",audience="gm")["text"]:
        raise ValueError("missing seven-lock source in indexed canonical SQLite")
    actual=game.entry("turn","s4.played.054",audience="gm")["text"]
    if "Кашель Гниющего Лотоса" not in actual or "Плачущие Бубоны" not in actual:
        raise ValueError("missing corrected new B-tier techniques")
    if "Минасэ Рэи, 19:51" in actual:
        raise ValueError("retracted ghost phone message became canon")
    returned=game.entry("turn","s4.played.026",audience="gm")["text"]
    if "20:28" not in returned or "Тайная лаборатория" not in returned:
        raise ValueError("corrected summons homecoming did not survive")
    with game.connection() as con:
        assert compaction.audit(con,root)
        turns=con.execute("SELECT count(*) FROM journal WHERE session=4 AND kind='turn'").fetchone()[0]
        assert turns==len(canon_ids)==61
        assert con.execute("SELECT status FROM processes WHERE id='s5.clinic.rear_unknown'").fetchone()[0]=="active"
    # The live HOT last scene is byte-preserved until the very first
    # s05.text.001 turn; HOT automatically starts the next session on it.
    data={
        "format":"npr-session4-replay-index/1",
        "raw_sha256":RAW_SHA,"raw_blob":RAW_BLOB,
        "source":"archive/session-004-source.md",
        "live_original":"archive/session-004-turns.jsonl",
        "full_original_prompt_count":83,"raw_response_count":83,
        "live_logged_rows":67,"live_missing":["s04.text.022"],
        "canonical_game_turns":len(canon_ids),"ooc_in_live":sorted(OOC),
        "recovered_missing":"archive/session-004-recovered-022.json",
        "canonical_026":sources[40]["source_ref"],
        "canonical_054":str(reviewed_path.relative_to(root)),
        "raw_correction_refs":[sources[i]["source_ref"] for i in (5,36,37,39,40,70,71,72,75,76,77,83)],
        "world_terminal":"1283-10-01T21:33:28","engine_terminal":END,
        "export_sha256":closed["sha256"],
        "index":index
    }
    (root/"archive/session-004-index.json").write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (root/"archive/session-004-recovered-022.json").write_text(json.dumps(recovered_022,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    handoff={
        "format":"npr-shiki-intersession-handoff/1",
        "closed":4,"next_session":5,"sqlite_revision":final["revision"],
        "at":END,"world_at":"Воскресенье, 1 октября 1283 года, 21:33:28",
        "location":END_PLACE,"next_live_turn":"s05.text.001",
        "current_scene":{
            "shiki":"лично открыл заднюю дверь клиники, обе руки на виду, спросил о возможности разговора, не атаковал",
            "rear_unknown":"за открытым порогом, перевязана левая кисть, знает фамилию Шики; требует прекратить интерес к Тэцу и В-7, угрожает родителям, его оружие и заказчик не установлены",
            "front_unknown":"у фасада в разговоре с Нори, отказался назвать отправителя",
            "nori":"снаружи у главного входа, не видела нынешнюю внешность Шики",
            "family":"Мугами Акихиро, Мугами Нао, Кирисава Сэйна и пациенты в запертой изнутри третьей палате"},
        "canon":{
            "seven_seals":"30 сентября Шики сам перенёс все семь запоров с Немерой в своё тело; бывший Нижний госпиталь по-прежнему полуфизичен",
            "nemera":"не была человеком, выбрала своё имя, цели независимы; сознание ограничено печатями",
            "arts":"1 октября освоены две B-техники: Ситон «Кашель Гниющего Лотоса» и Бётон «Плачущие Бубоны», обе с одноручными левыми печатями",
            "pact":"стадия 3; выраженные внешние изменения без разрушения собственной физиологии, слабый реальный запах при осознанной близости только как вторичный к предчувствию агонии",
            "retcons":"отменены вымышленные телефонные звонки из Нижнего госпиталя в тайную лабораторию; нет заявленного игроком разрешения на действия Шики за кадром",
            "tetsu":"1 октября утром Шики спас раненого Тэцу, он передан внутренней безопасности; слова незнакомцев об «умершем Тэцу» не доказательство действительной смерти"},
        "source_priority":["direct latest user correction","accepted played scenes","verified original raw transcript",
                           "HOT v2 and exact complete JSONL","SQLite exact replay","summary/GM plan"],
        "live_state_policy":"Do not mutate HOT.cursor or GM_ONLY independently at intersession. First new game turn s05.text.001 folds and swaps HOT.session to 5 automatically; old HOT shows closed session 4 until then.",
        "source_index":"archive/session-004-index.json",
        "archive":"archive/session-004.md",
    }
    out=root/"campaigns/shiki/continuity/SESSION_004_TO_005_CANON_1283-10-01.json"
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(handoff,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    # The long active-session chronicle already exists verbatim in the
    # append-only JSONL and chronicle archives. Keep only a small last-scene
    # tail in HOT; otherwise the old session may exceed the 72 KiB guard.
    # HOT stays at s04.text.068 until first s05.text.001, per GM_CORE.
    hot["chronicle"]=hot.get("chronicle",[])[-6:]
    hot["chronicle_older"]="archive/session-004-chronicle.md"
    hot["handoff"]={
        "closed":4,"next_session":5,"sqlite_revision":final["revision"],
        "at":END,"last_turn":"s04.text.068","first_turn":"s05.text.001",
        "source":"campaigns/shiki/continuity/SESSION_004_TO_005_CANON_1283-10-01.json"}
    (root/"recovery/HOT.json").write_text(
        json.dumps(hot,ensure_ascii=False,indent=1)+"\n",encoding="utf-8")
    from rpg_engine import hot as live_hot
    hot_report=live_hot.check(root)
    if not hot_report["ok"]:
        raise ValueError("post-close HOT continuity check failed: "+str(hot_report))
    # Binary canonical manifest and transport are published atomically with
    # all new source files. Historical HOT / GM_ONLY are NOT overwritten.
    (root/"recovery-before-s4.sqlite").unlink()
    manifest={"format":git_state.FORMAT,**game.verify(),"sha256":file_sha256(game.path)}
    (root/"state.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    shutil.rmtree(root/"recovery/sqlite",ignore_errors=True)
    git_state.write_transport(root,manifest)
    assert game.verify()=={k:final[k] for k in ("campaign_id","instance","revision","generation","session","at","pending","ok")}
    print("S4_TO_S5_SUCCESS",json.dumps({
        "revision":final["revision"],"generation":final["generation"],"session":final["session"],
        "at":final["at"],"canonical_turns":len(canon_ids),
        "ooc_skipped":len(OOC),"raw_prompts":83,
        "recovered_missing":"s04.text.022","corrected":[26,54],
        "compacted_sessions":final["compacted_sessions"],
        "sqlite_sha256":manifest["sha256"],"archive_sha256":closed["sha256"],
        "hot_unchanged":True},ensure_ascii=False),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("state",type=Path)
    main(parser.parse_args().state)
