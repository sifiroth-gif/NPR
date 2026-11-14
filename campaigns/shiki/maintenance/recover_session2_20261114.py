"""Recover Shiki's played Session II in the original private SQLite. No init/reset."""
import argparse, hashlib, json, re, shutil
from pathlib import Path
from rpg_engine.engine import Campaign, file_sha256
from rpg_engine import git_state, sessions

SHA='17379d3b0cc6dbefdbc2816ca7ac22ea9353138451d237800186139bb820e614'
DB_SHA='7903a57874be26ab9717a2978e0649da3f59c4241dfa383885190f24bd5f706e'
FINAL='0006-09-26T09:28:19'
SKIP={25,26,27,28,41,42,43,55,59,64,67,69,70,71,72,85,115,122,138,147,148,149}
MONTH={'июля':7,'августа':8,'сентября':9}
DATE=re.compile(r'(\d{1,2})\s+(июля|августа|сентября)\s+(1278|1283)\s+года',re.I)
TIME=re.compile(r'(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?!\d)')
OVERRIDE={68:'0006-09-19T07:23:00',144:'0006-09-26T03:36:00',153:FINAL}

def parse(source):
    ll=source.splitlines(keepends=True)
    ps=[i for i,s in enumerate(ll) if s.strip()=='## Prompt:']
    rs=[i for i,s in enumerate(ll) if s.strip()=='## Response:']
    assert len(ps)==153 and len(rs)==152 and len(ll)==25168
    out=[]
    for k,p in enumerate(ps,1):
        e=ps[k] if k<len(ps) else len(ll)
        r=next((j for j in rs if p<j<e),None)
        u=''.join(ll[p+2:r if r is not None else e]).strip()
        g=''
        if r is not None:
            a=''.join(ll[r+2:e]).strip().splitlines()
            times=[j for j,s in enumerate(a) if re.match(r'^\s*>?\s*Обработка заняла \d',s)]
            if times:a=a[times[-1]+1:]
            elif any(s.lstrip().startswith('> **') for s in a[:15]):
                cut=0
                for j,s in enumerate(a):
                    if s.startswith('>') or not s.strip():cut=j+1
                    else:break
                a=a[cut:]
            g=re.sub(r'\n---\s*Powered by \[ChatGPT Exporter\].*','', '\n'.join(a).strip(),flags=re.S).strip()
        out.append(dict(n=k,p=p,e=e,r=r,user=u,gm=g,ref=f'archive/session-002-source.md#L{p+1}-L{e}'))
    return ll,out

def timestamp(rec,prior):
    n=rec['n']
    if n in OVERRIDE:t=OVERRIDE[n]
    else:
        s=rec['gm'][:650]
        m=DATE.search(s)
        if not m:return prior
        day,mon,yr=m.groups()
        tail=s[m.end():m.end()+65]
        clocks=list(TIME.finditer(tail))
        v=clocks[-1].group() if len(clocks)>1 and ('–' in tail[:35] or '—' in tail[:35]) else (clocks[0].group() if clocks else '00:00:00')
        if v.count(':')==1:v+=':00'
        t=f'{int(yr)-1277:04}-{MONTH[mon]:02}-{int(day):02}T{v}'
    if t<prior:
        print('RETROACTIVE_HEADING',n,t,'=>',prior,flush=True)
        return prior
    return t

def place(rec,last):
    for s in rec['gm'].splitlines()[:8]:
        t=s.strip()
        if t.startswith('*') and t.endswith('*') and 'года' not in t:
            p=t.strip('*').strip()
            if 10<=len(p)<=400 and any(x in p for x in ['Конох','Кусаг','Страна ','госпитал','лаборатор','Ивовый','водоотвод','полигон']):return p
    return last

def fact(subject,name,value,hidden=False):
    return dict(op='set_fact',subject=subject,name=name,value=value,visibility='gm_only' if hidden else 'player_known')
def note(key,kind,subject,text,hidden=False,authority='played'):
    return dict(op='remember',key=key,category=kind,subject=subject,authority=authority,status='open',visibility='gm_only' if hidden else 'player_known',text=text)

def operations(n,state):
    x=[]
    if n==16:x += [fact('shiki','final_lee','Победа Шики над Роком Ли, гендзюцу, Шуншин, проволочная ловушка и скальпель.'),note('shiki.leefight','canon','shiki','Итог боя Ли — победа Шики. Ошибочный бой Naruto–Neji отменён.',authority='player')]
    if n==17:x += [fact('aki','gaara_injury','7 августа Гаара победил Аки, тяжёлая травма голени, медицинская помощь Шики.')]
    if n==35:x += [fact('shiki','final_dosu','7 августа 1278 Шики победил Досу.')]
    if n==39:x += [fact('shiki','final_gaara','Шики по собственному решению уступил в полуфинале Гааре; сказал «Я существую всегда».')]
    if n==49:x += [fact('ren','shoulder','Полностью восстановленное плечо; поздняя прямая поправка игрока.')]
    if n==66:x += [note('shiki.fiveyears','canon','shiki','Пятилетний скачок по прямому решению игрока, развитие боевых навыков, генетики и миссий; не придумывать за Шики новые добровольные действия.',authority='player')]
    if n==68:
        sheet=json.loads((state/'campaigns/shiki/characters/SHIKI_1283_AFTER_FIVE_YEAR_SKIP_2026-11-13.json').read_text())
        x += [fact('shiki','world_year',1283),fact('shiki','age',20),fact('shiki','rank','дзёнин'),fact('shiki','skills_current',sheet['stats_out_of_5']),fact('shiki','shunshin',sheet['speed_frame']),fact('shiki','mission_record',sheet['mission_history']),fact('shiki','secret_hyoton','Образец Хаку, освоенный Хьётон.',True),fact('shiki','secret_hydration','Ограниченная гидратация на базе образца Ходзуки.',True),fact('shiki','has_sharingan',False,True)]
    if n==78:x += [fact('case.inaba','investigation','Официальное расследование 19 сентября 1283 года открыто.')]
    if n==110:x += [fact('shiki','s17_self_test','Однократная проба С-17: 0,5 мл материала в 10 мл раствора, 22 сентября 1283.'),note('case.s17','thread','case.s17','Функциональные механизмы двух фракций изучаются, конкретные вещества/производитель не доказаны.')]
    if n==112:x += [fact('shiki','s17_clearance','Нулевой Геном вывел С-17 после опыта.',True)]
    if n==119:x += [fact('shiki','own_dna_rewrite','Внесение основ льда и гидратации в собственную ядерную ДНК клеток-предшественников, проверено наследование дочерними клетками.',True),note('shiki.genome','canon','shiki','Нулевой Геном позволяет волевое редактирование собственной ДНК; без автоматических новых добровольных изменений.',True,'player')]
    if n==120:x += [fact('shiki','own_dna_rewrite','К 25 сентября 1283 завершено общесоматическое распространение модификаций собственной ядерной ДНК; гидратация остаётся лишь частичной, Хьётон ограничен теплом, влагой и чакрой.',True),fact('shiki','chakra','восстановлена полностью')]
    if n==121:x += [fact('case.s17','results','Предварительно две фракции S17-A/S17-B с разной нейромышечной активностью; прямые молекулярные мишени не доказаны.')]
    if n==137:x += [fact('shiki','intruders','Двое злоумышленников пытались подменить документы; задержаны живыми, переданы безопасности, улики осмотрены в перчатках.'),note('case.falsepapers','thread','case.inaba','Попытка подмены результатов и реагенты; личные знания NPC не расширять автоматически.')]
    if n==144:x += [fact('shiki','home_seals','Новая архитектура четырёх защитных уровней завершена около 03:36; действие игрока подтверждено следующей сценой.',True)]
    if n==150:x += [fact('northern.guard','wound','Шики вылечил ранение предплечья патрульного 26.09.1283.')]
    if n==152:x += [fact('hirose','status','погиб от осколка решётки во время восточной стычки'),fact('fugitive','status','восточные шиноби преследуют у склада водоснабжения; исход открыт'),fact('northern.captive','injury','жив, ампутация трёх пальцев, рана затылка, возможное повреждение головы')]
    if n==153:x += [fact('artifact.v7','observed',{'label':'В-7','length_cm':70,'mass_kg_approx':6,'closed':True,'locks':2,'conductive_thread':True,'chakra_cooling':True,'contents':'unknown','hazard':'unconfirmed'}),fact('shiki','scene',{'at':'1283-09-26T09:28:19','location':'северный коллектор Кусагакуре','v7_opened':False,'nori_question':'насколько опасна печать?'}),note('shiki.s3scene','scene','shiki','26 сентября 1283 09:28:19; Шики держит закрытый В-7, Нори просит оценить опасность; пленный эвакуируется, погоня продолжается.',authority='player')]
    return x

def source_span(lines,lo,hi,needle):
    s=''.join(lines[lo:hi]);pos=s.find(needle)
    if pos<0:return None
    a=s[:pos];b=s[:pos+len(needle)]
    return [lo+a.count('\n')+1,len(a.rsplit('\n',1)[-1])+1,lo+b.count('\n')+1,len(b.rsplit('\n',1)[-1])+1]

def main(state):
    raw=(state/'archive/session-002-source.md').read_text()
    assert hashlib.sha256(raw.encode()).hexdigest()==SHA
    game=Campaign(state/'campaign.sqlite')
    current=game.verify()
    manifest=json.loads((state/'state.json').read_text())
    assert manifest=={'format':git_state.FORMAT,**current,'sha256':file_sha256(game.path)}
    if current['session']==3 and current['at']==FINAL:print('ALREADY_RECOVERED',current);return
    assert current['revision']==85 and current['session']==2 and current['at']=='0001-07-28T18:18:00' and not current['pending'] and manifest['sha256']==DB_SHA
    game.checkpoint(state/'recovery-before-s2.sqlite')
    lines,rows=parse(raw)
    at=current['at'];loc='Конохагакуре, тренировочный полигон у западного склона'
    stamps={};played=0;skipped=[]
    for rec in rows:
        n=rec['n']
        if n in SKIP:skipped.append(n);continue
        nextat=timestamp(rec,at)
        nextloc=place(rec,loc)
        if n==144:nextloc='Кусагакуре, дом Мугами Шики'
        if n==153:nextloc='Кусагакуре, северный коллектор старого водоотвода, 25 м от входа'
        with game.connection() as c:
            pending=list(c.execute("SELECT p.id,p.due_at,m.id AS is_message FROM processes p LEFT JOIN messages m ON m.id=p.id WHERE p.status='active' AND p.due_at < ? ORDER BY p.due_at,p.id",(nextat,)))
        ev=[]
        for item in pending:
            if item['is_message']:raise RuntimeError('Pending message requires exact delivery evidence: '+item['id'])
            ev.append(dict(id='s2.due.'+str(n)+'.'+item['id'],at=item['due_at'],visibility='gm_only',
                summary='Устаревшая календарная граница старого плана закрыта при реплее; исход NPC не предполагается.',source_ref=rec['ref'],
                operations=[{'op':'finish_process','id':item['id'],'status':'cancelled','outcome':{'reason':'historical schedule superseded; no autonomous action inferred','source_ref':rec['ref']}}]))
        ops=operations(n,state)
        if n==80:
            with game.connection() as c:
                exists=c.execute("SELECT 1 FROM npcs WHERE id='nori'").fetchone()
            if not exists:ops.insert(0,dict(op='upsert_npc',npc={'id':'nori','name':'Хаясэ Нори','profile':{'role':'капитан внутренней безопасности','goal':'спасти людей и собрать доказательства','limits':'нет знания содержимого В-7, тайных исследований и будущих событий'}}))
        if n==153:
            ops += [
                {'op':'start_process','id':'s3.medical_evac','owner':'nori','goal':'Эвакуировать и стабилизировать тяжелораненого пленного','due_at':'0006-09-26T09:29:00','visibility':'gm_only','notes':'Только реальная медицинская динамика; решения Шики за игроком.'},
                {'op':'start_process','id':'s3.eastern_pursuit','owner':'nori','goal':'Получить донесение и преследовать беглеца на востоке','due_at':'0006-09-26T09:29:20','visibility':'gm_only','notes':'Автономное преследование, результат ещё не установлен.'}]
        if ops:ev.append(dict(id=f's2.canon.{n:03}',at=nextat,visibility='gm_only' if n in {68,119,120,144,153} else 'player_known',
            summary=f'Сыгранные последствия записи s2.{n:03}; источник в архиве.',source_ref=rec['ref'],operations=ops))
        reply=rec['gm'] or '[Восстановление из сыгранного диалога] Сохранена прямая заявка игрока. В первичном экспорте отдельного ответа нет; последствия подтверждены позднее.'
        packet={'id':f's2.played.{n:03}','expected_revision':current['revision'],'at':nextat,'location':nextloc,
            'player_input':rec['user'] or 'Событие из архива','text':reply,'input_mode':'direction',
            'source_ref':rec['ref'],'source_status':'exact' if rec['gm'] else 'reconstructed','events':ev}
        if n==153:packet['scene']={'id':'s3.collector.v7','actors':['shiki','nori'],'beat':'decision','focus':'Нори спрашивает об опасности запечатанного В-7, пленный эвакуируется, восточная погоня продолжается.'}
        try:
            receipt=game.prepare(packet)
            game.confirm(packet['id'],receipt['draft_sha'])
        except Exception as err:raise RuntimeError(f'REPLAY_FAIL n={n} rev={current["revision"]} at={nextat} events={ev}') from err
        current['revision']+=1;at=nextat;loc=nextloc;stamps[n]=at;played+=1
        if played%20==0:print('REPLAY',played,at,'REV',current['revision'],flush=True)
    assert at==FINAL,(at,FINAL)
    passages=[]
    for rec in rows:
        n=rec['n']
        if n not in stamps:continue
        for role,needle,lo,hi in [('player',rec['user'],rec['p']+2,rec['r'] if rec['r'] is not None else rec['e']),
                                  ('gm',rec['gm'],(rec['r']+2) if rec['r'] is not None else rec['e'],rec['e'])]:
            if not needle or len(needle)>30000:continue
            span=source_span(lines,lo,hi,needle)
            if span:passages.append({'role':role,'at':stamps[n],'ranges':[span],'text':needle,'entities':['shiki']})
    source={'format':'npr-session-transcript/1','session':2,'source':'archive/session-002-source.md','source_sha256':SHA,'passages':passages}
    closing=sessions.close(game,current['revision'],state/'archive/session-002.md',transcript=source)
    final=game.verify()
    assert final['session']==3 and final['at']==FINAL and not final['pending']
    assert game.entry('turn','s2.played.153',audience='gm')['text']==rows[-1]['gm']
    with game.connection() as c:
        assert c.execute("SELECT count(*) FROM journal WHERE id LIKE 'transcript.s2.%'").fetchone()[0]==len(passages)
    (state/'recovery-before-s2.sqlite').unlink()
    new_manifest={'format':git_state.FORMAT,**final,'sha256':file_sha256(game.path)}
    (state/'state.json').write_text(json.dumps(new_manifest,ensure_ascii=False,indent=2)+'\n')
    shutil.rmtree(state/'recovery/sqlite',ignore_errors=True)
    git_state.write_transport(state,new_manifest)
    assert game.verify()==final
    print('RECOVERY_SUCCESS',json.dumps({'closing':closing,'final':final,'played_turns':played,'transcript_passages':len(passages),'skipped_source_records':skipped},ensure_ascii=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('state',type=Path);main(p.parse_args().state)
