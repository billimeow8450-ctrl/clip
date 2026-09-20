"""192 measured looks in 32 typographic recipes; no unsupported trend claim."""
import math
import hashlib
import re
from dataclasses import asdict
from statistics import median
from .models import CaptionStyle
from .caption_history import media_fingerprint,choose_diverse

PALETTES=(
    ("lime","#43FF4B",123),("gold","#FFF04D",55),("ice","#77ECFF",189),
    ("coral","#FF875F",15),("orchid","#DEB1FF",274),("mint","#82F8CE",159),
)
# name, font, size, words, animation, plate, upper, italic, weight, phrase, emphasis, outline
FAMILIES=(
    ("reference_bold","Montserrat",74,4,"highlight","none",True,False,800,"balanced","active",5),
    ("compact_stack","Montserrat",68,5,"highlight","none",True,False,800,"stack","active",5),
    ("editorial_sentence","Montserrat",62,7,"fade_word","none",False,False,600,"sentence","keyword",3),
    ("cinema_serif","DM Serif Display",70,5,"phrase_fade","none",False,False,400,"balanced","none",3),
    ("rounded_plate","Montserrat",66,5,"highlight","rounded",False,False,700,"balanced","active",2),
    ("news_banner","Barlow Condensed",72,7,"underline","banner",True,False,700,"sentence","keyword",2),
    ("marker_line","Montserrat",70,4,"marker","none",False,False,800,"balanced","active",4),
    ("duotone_pulse","Anton",76,3,"pulse","none",True,False,400,"balanced","active",4),
    ("soft_karaoke","Montserrat",64,6,"progressive","none",False,False,600,"sentence","active",3),
    ("mono_signal","DejaVu Sans Mono",62,5,"underline","panel",True,False,700,"balanced","keyword",2),
    ("clean_outline","Montserrat",72,4,"steady","none",True,False,800,"balanced","none",4),
    ("double_deck","Barlow Condensed",76,6,"two_tone","none",True,False,700,"stack","last_line",4),
    ("italic_impact","Barlow Condensed",80,3,"steady","none",True,True,700,"short","all",4),
    ("word_serif","DM Serif Display",78,2,"steady","none",False,False,400,"short","all",4),
    ("clean_lowercase","Montserrat",64,5,"fade_word","none",False,False,600,"balanced","keyword",3),
    ("word_focus","Anton",78,1,"pulse","none",True,False,400,"single","all",4),
    ("quiet_sentence","Montserrat",60,7,"steady","none",False,False,600,"sentence","none",3),
    ("highlight_box","Montserrat",70,4,"word_box","none",True,False,800,"balanced","keyword",3),
    ("thin_divider","Montserrat",64,6,"rule","none",False,False,700,"sentence","none",3),
    ("story_quote","DM Serif Display",72,5,"fade_word","none",False,True,400,"balanced","keyword",3),
    ("caption_ribbon","Montserrat",64,6,"steady","banner",False,False,700,"sentence","none",1),
    ("stack_emphasis","Montserrat",74,5,"two_tone","none",True,False,800,"stack","last_line",5),
    ("light_documentary","DM Serif Display",66,6,"steady","none",False,False,400,"sentence","none",2),
    ("contrast_card","Montserrat",68,5,"highlight","light",True,False,800,"balanced","keyword",0),
    ("luxe_serif","DM Serif Display",74,4,"phrase_fade","none",False,True,400,"balanced","keyword",3),
    ("fashion_stack","Barlow Condensed",78,4,"two_tone","none",True,False,700,"stack","last_line",4),
    ("clean_pop","Montserrat",72,3,"pulse","rounded",True,False,800,"short","active",2),
    ("social_quote","DM Serif Display",70,6,"fade_word","rounded",False,True,400,"sentence","keyword",2),
    ("editorial_label","Barlow Condensed",68,6,"underline","panel",True,False,700,"sentence","keyword",2),
    ("neon_minimal","Anton",72,2,"steady","none",True,False,400,"short","all",4),
    ("boutique_lower","Montserrat",66,5,"phrase_fade","none",False,False,600,"balanced","keyword",3),
    ("documentary_bold","Montserrat",70,6,"progressive","none",False,False,800,"sentence","active",4),
)

def ass_colour(value,alpha="00"):
    rgb=value.lstrip("#")
    return f"&H{alpha}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}"

def catalog():
    result={}
    for family,font,size,limit,animation,plate,upper,italic,weight,phrase,emphasis,outline in FAMILIES:
        for n,(palette,accent,hue) in enumerate(PALETTES):
            dark=plate=="light"
            if dark:
                accent="#"+''.join(f'{int(int(accent[i:i+2],16)*.40):02X}' for i in (1,3,5))
            primary="&H00181818" if dark else "&H00FFFFFF"
            if emphasis=="all":
                primary=ass_colour(accent)
            name=family+"_"+palette
            result[name]=CaptionStyle(
                name=name,font=font,size=min(82,size-4+n*2),
                min_words=1 if limit<=3 else 2,max_words=limit,
                max_chars=18 if limit<=3 else 30 if limit<=5 else 40,
                primary=primary,accents=[ass_colour(accent),"&H00F4F3FF"],
                outline="&H00FFFFFF" if dark else "&H00101010",outline_size=outline,
                shadow=0 if plate!="none" else 1,animation=animation,
                y_normal=1480,y_split=960,uppercase=upper,
                active_peak_scale=104,
                active_settle_scale=100,normal_scale=100,lines=2,
                family=family,palette=palette,font_weight=weight,
                spacing=round(-.5+n*.1,2),italic=italic,plate=plate,
                plate_colour="&H05FFFFFF" if dark else "&H38111111",
                max_width=800 if phrase=="sentence" else 780,
                accent_hue=hue,energy=.4 if animation=="pulse" else .16 if emphasis=="none" else .28,
                busy_safe=plate!="none" or outline>=4,
                phrase_mode=phrase,emphasis=emphasis,
                description=f"{family.replace('_',' ')}; {font}; {phrase} phrasing; {emphasis} emphasis",
            )
    return result

def _rgb(ass):
    value=ass.replace("&H","")[-6:]
    return tuple(int(value[i:i+2],16)/255 for i in (4,2,0))

def _luminance(rgb):
    linear=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb]
    return sum(a*b for a,b in zip((.2126,.7152,.0722),linear))

def select_style(media,profile,transcript,samples,*,history_path=None,scope="default",job_key=None):
    styles=catalog()
    luma=median(row.caption_luma for row in samples) if samples else .4
    busy=median(row.caption_edges for row in samples) if samples else .08
    saturation=median(row.saturation for row in samples) if samples else .15
    sharpness=median(row.sharpness for row in samples) if samples else .3
    weighted=[(math.radians(row.dominant_hue),max(.01,row.saturation)) for row in samples]
    hue=math.degrees(math.atan2(sum(math.sin(a)*w for a,w in weighted),sum(math.cos(a)*w for a,w in weighted)))%360 if weighted else 0
    low_detail=min(media.width,media.height)<720 or sharpness<.12
    wpm=len(transcript.words)/max(.01,media.duration/60)
    words=set(re.findall(r"[a-z]+",transcript.text.lower()))
    emotional=bool(words.intersection({"mother","father","daughter","son","family","life","died","love","lost"}))
    factual=bool(words.intersection({"research","data","percent","science","business","money","study"}))
    preferences={
        "podcast_interview":{"reference_bold","compact_stack","italic_impact","word_serif","clean_lowercase","soft_karaoke","stack_emphasis","highlight_box","fashion_stack","clean_pop","boutique_lower"},
        "story_commentary":{"reference_bold","compact_stack","italic_impact","word_serif","story_quote","clean_lowercase","stack_emphasis","cinema_serif","luxe_serif","social_quote"},
        "finance_business":{"editorial_sentence","mono_signal","news_banner","thin_divider","clean_lowercase","quiet_sentence","editorial_label","documentary_bold"},
        "education_explainer":{"editorial_sentence","marker_line","soft_karaoke","highlight_box","quiet_sentence","clean_lowercase","documentary_bold"},
        "news_documentary":{"news_banner","quiet_sentence","editorial_sentence","caption_ribbon","light_documentary","thin_divider","editorial_label","documentary_bold"},
        "beauty_fashion":{"cinema_serif","clean_lowercase","story_quote","clean_outline","soft_karaoke","luxe_serif","social_quote","boutique_lower"},
        "gaming_sports":{"duotone_pulse","word_focus","italic_impact","double_deck","marker_line","clean_pop","neon_minimal"},
    }.get(profile.category,{"reference_bold","clean_lowercase","soft_karaoke","editorial_sentence","word_serif"})
    rankings=[]
    for style in styles.values():
        # Faster material cannot use a strobing one-word recipe.
        if style.max_words==1 and wpm>155:
            continue
        if busy>.18 and not style.busy_safe:
            continue
        bg=.025 if style.outline_size>=3 or style.plate not in {"none","light"} else max(.01,luma**2.2)
        if style.plate=="light":
            bg=.95
        fg=_luminance(_rgb(style.accents[0] if style.emphasis!="none" else style.primary))
        contrast=(max(fg,bg)+.05)/(min(fg,bg)+.05)
        if contrast<3.2:
            continue
        score=5.0 if style.family in preferences else 0.0
        score+=min(7,contrast)*.25
        score+=abs((style.accent_hue-hue+180)%360-180)/180*saturation*.8
        if busy>.13:
            score+=1.0 if style.plate!="none" else .5 if style.outline_size>=4 else -1
            score+=1.0 if style.font_weight>=700 else -1.0
        else:
            score+=.7 if style.plate=="none" else -.8
            if luma<.25 and style.font in {'DM Serif Display','Montserrat'} and style.font_weight<=600:
                score+=.8
        if low_detail:
            score+=.5 if style.size>=68 and style.outline_size>=3 else 0
        if wpm>175:
            score+=.8 if style.max_words>=4 else -1.5
        if emotional and style.family in {"reference_bold","compact_stack","story_quote","cinema_serif","stack_emphasis"}:
            score+=.9
        if factual and style.family in {"editorial_sentence","quiet_sentence","thin_divider"}:
            score+=.8
        if wpm<115 and style.family in {"word_serif","italic_impact","clean_lowercase"}:
            score+=.6
        rankings.append({"template":style.name,"score":round(score,4),"accent_contrast_estimate":round(contrast,2)})
    rankings.sort(key=lambda row:(-row["score"],row["template"]))
    if not rankings:
        rankings=[{"template":"reference_bold_lime","score":0,"accent_contrast_estimate":7}]
    best=rankings[0]["score"]
    eligible=[row for row in rankings if row["score"]>=best-2.4 and styles[row["template"]].family in preferences]
    if not eligible:
        eligible=rankings[:6]
    fingerprint=media_fingerprint(media,transcript)
    if job_key is not None:
        fingerprint=hashlib.sha256((fingerprint+str(job_key)).encode()).hexdigest()
    selected,diversity=choose_diverse(eligible,styles,fingerprint,path=history_path,scope=scope)
    return styles[selected],{
        "template_count":len(styles),"family_count":len(FAMILIES),
        "selected_template":selected,"selected_family":styles[selected].family,
        "measured_caption_luma":round(luma,3),"measured_caption_edges":round(busy,3),
        "dominant_hue_degrees":round(hue,1),"source_low_detail":low_detail,
        "selection_policy":"readability first; rotate visible look and colour across new jobs; retries keep their saved choice",
        "eligible_families":sorted({styles[row["template"]].family for row in eligible}),
        "media_fingerprint":fingerprint,"diversity":diversity,
        "top_candidates":rankings[:6],"live_trend_scraping":False,
    }

def export_catalog():
    styles=catalog()
    return {"version":"7.8.0","count":len(styles),"design_families":len(FAMILIES),
        "templates":{name:asdict(style) for name,style in styles.items()}}
