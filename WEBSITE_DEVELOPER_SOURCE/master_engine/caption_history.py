"""Creator-scoped, transactional caption variety; stable on retries of a video."""
import hashlib
import sqlite3
import time


def media_fingerprint(media,transcript):
    digest=hashlib.sha256()
    digest.update(f'{media.width}:{media.height}:{media.duration:.3f}'.encode())
    digest.update(transcript.text.strip().encode('utf-8'))
    if media.path.is_file():
        size=media.path.stat().st_size
        digest.update(str(size).encode())
        with media.path.open('rb') as handle:
            for at in sorted({0,max(0,size//2-32768),max(0,size-65536)}):
                handle.seek(at); digest.update(handle.read(65536))
    return digest.hexdigest()


def choose_diverse(pool,styles,key,*,path=None,scope='default'):
    # The pool has already passed readability/category/speed checks. Diversity
    # never admits arbitrary templates from the full catalogue.
    ranked=[dict(row) for row in pool]
    base=int(key[:16],16)
    note={'history_enabled':bool(path),'repeat_avoided':False,'same_video_reused':False}
    if path is None:
        return ranked[base%min(8,len(ranked))]['template'],note
    try:
        path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path,timeout=10) as db:
            db.execute('CREATE TABLE IF NOT EXISTS choices (scope TEXT, media TEXT, template TEXT, family TEXT, used REAL, PRIMARY KEY(scope,media))')
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT template FROM choices WHERE scope=? AND media=?',(scope,key)).fetchone()
            if existing and any(row['template']==existing[0] for row in ranked):
                note['same_video_reused']=True
                return existing[0],note
            recent=db.execute('SELECT template,family FROM choices WHERE scope=? ORDER BY used DESC LIMIT 24',(scope,)).fetchall()
            prior_family=recent[0][1] if recent else None
            def look(style):
                if style.family in {'reference_bold','compact_stack','stack_emphasis','clean_outline'}:
                    return 'bold_stack'
                return (style.font,style.uppercase,style.italic,style.plate)
            def colour(style):
                return 'green' if style.palette in {'lime','mint'} else style.palette
            recent_styles=[styles[name] for name,_ in recent if name in styles]
            candidates=ranked
            # Rotate visible typography, not just two names for white bold text.
            for count in (2,1):
                blocked={look(s) for s in recent_styles[:count]}
                fresh=[row for row in candidates if look(styles[row['template']]) not in blocked]
                if fresh:
                    candidates=fresh
                    break
            fresh=[row for row in candidates if styles[row['template']].family!=prior_family]
            if fresh:
                candidates=fresh
            if recent_styles:
                fresh=[row for row in candidates if colour(styles[row['template']])!=colour(recent_styles[0])]
                if fresh:
                    candidates=fresh
            used={name for name,_ in recent}
            fresh=[row for row in candidates if row['template'] not in used]
            if fresh:
                candidates=fresh
            # Prefer recently unused families among comparable safe styles.
            counts={family:sum(old==family for _,old in recent) for _,family in recent}
            for row in candidates:
                row['diversity_score']=row['score']-counts.get(styles[row['template']].family,0)*1.2
            candidates.sort(key=lambda row:(-row['diversity_score'],row['template']))
            best=candidates[0]['diversity_score']
            close=[row for row in candidates if row['diversity_score']>=best-.6]
            selected=close[base%len(close)]['template']
            family=styles[selected].family
            db.execute('INSERT OR REPLACE INTO choices VALUES (?,?,?,?,?)',(scope,key,selected,family,time.time()))
            db.execute('DELETE FROM choices WHERE scope=? AND media NOT IN (SELECT media FROM choices WHERE scope=? ORDER BY used DESC LIMIT 300)',(scope,scope))
            note.update(repeat_avoided=bool(prior_family and family!=prior_family),recent_family=prior_family,
                        palette=styles[selected].palette,visible_look_rotation=True)
            return selected,note
    except (sqlite3.Error,OSError) as exc:
        note.update(history_enabled=False,history_warning=type(exc).__name__)
        return ranked[base%min(8,len(ranked))]['template'],note
