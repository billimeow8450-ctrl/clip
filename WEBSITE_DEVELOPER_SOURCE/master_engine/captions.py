"""Measured two-line subtitles with stable phrase geometry and safe ASS tags."""
from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import List, Sequence, Tuple

from .composition import caption_y
from .fonts import bundled_font,render_family,ass_metric_scale
from .models import CaptionStyle, GraphicEvent, LayoutSegment, Word


def _time(value: float) -> str:
    total = max(0, round(value*100))
    hours, rem = divmod(total, 360000)
    minutes, rem = divmod(rem, 6000)
    seconds, centis = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{seconds:02d}.{centis:02d}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", r"\{").replace("}", r"\}").replace("\n", " ")


@lru_cache(maxsize=128)
def _font_path(family: str, italic: bool = False, weight: int = 700) -> str:
    local = bundled_font(family, weight, italic)
    if local:
        return local
    pattern = family+":style="+("Bold" if weight>=700 else "Regular")+(" Italic" if italic else "")
    try:
        path = subprocess.check_output(["fc-match", "-f", "%{file}", pattern], text=True, timeout=5).strip()
        if Path(path).is_file():
            return path
    except (OSError, subprocess.SubprocessError):
        pass
    for path in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"):
        if Path(path).is_file():
            return path
    return ""


@lru_cache(maxsize=1024)
def _font(family: str, size: int, italic: bool, weight: int = 700):
    from PIL import ImageFont
    path = _font_path(family, italic, weight)
    return ImageFont.truetype(path, size) if path else ImageFont.load_default()


def _width(text, style, size):
    font = _font(style.font, size, style.italic, style.font_weight)
    factor = ass_metric_scale(_font_path(style.font, style.italic, style.font_weight))
    return float(font.getlength(text))*factor + max(0, len(text)-1)*style.spacing


def _layout_at(layouts: Sequence[LayoutSegment], value: float) -> LayoutSegment:
    for row in layouts:
        if row.source_start-.001 <= value < row.source_end-.001:
            return row
    return layouts[-1] if layouts else LayoutSegment(0, 1e9, "focus")


def _groups(words: Sequence[Word], style: CaptionStyle, layouts: Sequence[LayoutSegment]) -> List[List[Word]]:
    groups: List[List[Word]] = []
    group: List[Word] = []
    for word in sorted(words, key=lambda item:(item.start,item.end)):
        clean = re.sub(r"\s+", " ", word.text).strip()
        if not clean or word.end <= word.start:
            continue
        word = replace(word, text=clean, start=max(0,word.start))
        if group:
            new_layout = _layout_at(layouts,word.start) is not _layout_at(layouts,group[0].start) if layouts else False
            gap = word.start-group[-1].end
            if _measure(group+[word],style)[2]>style.max_width or new_layout or len(group) >= style.max_words or sum(len(w.text)+1 for w in group)+len(clean) > style.max_chars or gap > .5 or word.end-group[0].start > 2.8:
                groups.append(group)
                group = []
        group.append(word)
        if clean.endswith((".","?","!",";")) and len(group) >= style.min_words:
            groups.append(group)
            group = []
    if group:
        groups.append(group)
    return groups


def _measure(group, style):
    texts = [word.text.upper() if style.uppercase else word.text for word in group]
    size, split = style.size, -1
    width = _width(" ".join(texts), style, size)
    if len(texts)>1 and (width>style.max_width*.85 or style.phrase_mode=="stack"):
        options = []
        for at in range(1,len(texts)):
            a = _width(" ".join(texts[:at]),style,size)
            b = _width(" ".join(texts[at:]),style,size)
            options.append((max(a,b)+abs(a-b)*.15,at,max(a,b)))
        _, split, width = min(options)
        if style.family in {"reference_bold","stack_emphasis","double_deck"}:
            first = _width(" ".join(texts[:-1]),style,size)
            last = _width(texts[-1],style,size)
            if max(first,last)+2*style.outline_size+8<=style.max_width:
                split,width = len(texts)-1,max(first,last)
    return size,split,width+2*style.outline_size+8,_caption_height(style,size,2 if split>0 else 1)

def _caption_step(style, size):
    font = _font(style.font,size,style.italic,style.font_weight)
    factor = ass_metric_scale(_font_path(style.font,style.italic,style.font_weight))
    box = font.getbbox("H")
    return (box[3]-box[1])*factor*1.15+2*style.outline_size

def _caption_height(style, size, lines):
    font = _font(style.font,size,style.italic,style.font_weight)
    factor = ass_metric_scale(_font_path(style.font,style.italic,style.font_weight))
    box = font.getbbox("Agyp")
    return (lines-1)*_caption_step(style,size)+(box[3]-box[1])*factor+2*style.outline_size+10

def _reference_style(style, words, height):
    font = _font(style.font,1000,style.italic,style.font_weight)
    factor = ass_metric_scale(_font_path(style.font,style.italic,style.font_weight))
    box = font.getbbox("H")
    # Match the large, readable podcast-caption references at 1080x1920.
    # This cap-height is fixed once for the whole job, so successive phrases
    # never shrink/grow while the gentle 100->104->100 emphasis remains.
    size = max(12,round(68*height/1920*1000/max(1,(box[3]-box[1])*factor)))
    # An exceptional unbroken token may reduce the WHOLE job's size once.
    # Never shrink and grow successive phrases to make them fit.
    tokens = [token.upper() if style.uppercase else token for w in words for token in w.text.split()]
    while size>12 and any(_width(t,style,size)+2*style.outline_size+8>style.max_width for t in tokens):
        size -= 1
    return replace(style,size=size,active_peak_scale=104,active_settle_scale=100,normal_scale=100)


def _phrase_zoom(start, end, at, until):
    """One 100->104->100 pulse per phrase, continuous across word/cut events."""
    if end-start < .50:
        return r'\fscx100\fscy100'
    knots=((start,100.0),(start+.14,104.0),(start+.38,100.0))
    def value(t):
        for (a,x),(b,y) in zip(knots,knots[1:]):
            if t <= b:
                return x+(y-x)*max(0,min(1,(t-a)/(b-a)))
        return 100.0
    # ASS dialogue times are centiseconds. Use the same clock for every word.
    at=round(at*100)/100
    until=round(until*100)/100
    scale=value(at)
    tags=rf'\fscx{scale:.3f}\fscy{scale:.3f}'
    previous=at
    for stop in sorted({min(until,t) for t,_ in knots if t>at}):
        if stop>previous:
            a,b=round((previous-at)*1000),round((stop-at)*1000)
            target=value(stop)
            tags+=rf'\t({a},{b},\fscx{target:.3f}\fscy{target:.3f})'
            previous=stop
    return tags


def _caption_y(mode: str, style: CaptionStyle, height: int) -> int:
    return round(caption_y(mode)*height/1920)


def _intervals(layouts, start, end):
    """A word spanning a cut must change its position on that very cut."""
    edges=sorted({start,end,*[row.source_start for row in layouts if start<row.source_start<end]})
    return [(a,b,_layout_at(layouts,(a+b)/2)) for a,b in zip(edges,edges[1:])]


def _keyword(group):
    common=set('a an the is are was were be been to of and but or in on at for it this that i you he she we they my your with not can do did have has'.split())
    return max(range(len(group)),key=lambda i:(bool(re.search(r'\d',group[i].text)),
        group[i].text.lower().strip('.,?!') not in common,len(group[i].text),-i))


def _word_rectangle(group, index, split, style, size, x, y):
    texts=[word.text.upper() if style.uppercase else word.text for word in group]
    lo=0 if split<0 or index<split else split
    hi=len(texts) if split<0 or index>=split else split
    font=_font(style.font,size,style.italic,style.font_weight)
    # libass normalizes font size to the face's Windows ascender+descender; Pillow
    # uses em-size. Account for that difference before placing a word plate.
    factor=ass_metric_scale(_font_path(style.font,style.italic,style.font_weight))
    def width(value):
        return float(font.getlength(value))*factor+max(0,len(value)-1)*style.spacing
    line_width=width(' '.join(texts[lo:hi]))
    prefix=width(' '.join(texts[lo:index])+(' ' if index>lo else ''))
    word_width=width(texts[index])
    step=_caption_step(style,size)
    cy=y if split<0 else y+(-1 if index<split else 1)*step*.5
    box=font.getbbox('Hg')
    return x-line_width/2+prefix+word_width/2,cy,word_width+8,(box[3]-box[1])*factor+8


def _dialogue(layer: int, start: float, end: float, content: str) -> str:
    return f"Dialogue: {layer},{_time(start)},{_time(end)},Master,,0,0,0,,{content}"


def _plate(start: float, end: float, x: float, y: float, w: float, h: float, style: CaptionStyle, pad_x: int = 18, pad_y: int = 8) -> str:
    left, top, right, bottom = round(x-w/2-pad_x), round(y-h/2-pad_y), round(x+w/2+pad_x), round(y+h/2+pad_y)
    if style.plate in {"rounded","panel"}:
        r=18
        shape = f"m {left+r} {top} l {right-r} {top} b {right} {top} {right} {top} {right} {top+r} l {right} {bottom-r} b {right} {bottom} {right} {bottom} {right-r} {bottom} l {left+r} {bottom} b {left} {bottom} {left} {bottom} {left} {bottom-r} l {left} {top+r} b {left} {top} {left} {top} {left+r} {top}"
    else:
        shape=f"m {left} {top} l {right} {top} {right} {bottom} {left} {bottom}"
    colour=style.plate_colour[-6:]
    alpha=style.plate_colour.replace("&H", "")[:2]
    return _dialogue(0,start,end,rf"{{\an7\pos(0,0)\p1\bord0\shad0\1c&H{colour}&\alpha&H{alpha}&\fad(60,60)}}"+shape+"{\\p0}")


def write_ass(words: Sequence[Word], layouts: Sequence[LayoutSegment], style: CaptionStyle, path: Path, *, width: int = 1080, height: int = 1920, graphics: Sequence[GraphicEvent] = ()) -> Path:
    path.parent.mkdir(parents=True,exist_ok=True)
    style = replace(style,max_width=min(style.max_width, width-280)/1.04)
    font = render_family(style.font,style.font_weight) if not any(ord(char)>0x024F for word in words for char in word.text) else "Noto Sans"
    style = _reference_style(replace(style,font=font),words,height)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Master,{font},{style.size},{style.primary},{style.primary},{style.outline},&H60000000,{style.font_weight},{-1 if style.italic else 0},0,0,100,100,{style.spacing},0,1,{style.outline_size},{style.shadow},5,140,140,120,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: List[str] = []
    groups = _groups(words,style,layouts)
    previous_end = 0.0
    for group_index, group in enumerate(groups):
        size, split, measured_w, measured_h = _measure(group,style)
        start = max(previous_end,group[0].start)
        next_start = groups[group_index+1][0].start if group_index+1 < len(groups) else float("inf")
        end = min(next_start,max(start+.06,group[-1].end))
        if 0<=next_start-end<=.16:
            end=next_start  # bridge sub-frame/phoneme gaps, not real pauses
        if end <= start:
            continue
        keyword=_keyword(group)
        active_timing=style.emphasis=='active' or style.family=='word_focus'
        periods=[(max(start,word.start),min(end,group[active+1].start if active+1<len(group) else end),active)
            for active,word in enumerate(group)] if active_timing else [(start,end,keyword)]
        for section_start,section_end,layout in _intervals(layouts,start,end):
            y=round(layout.caption_y*height/1920) if layout.caption_y else _caption_y(layout.mode,style,height)
            # Text bottom, including two lines and plate, stays above mobile UI.
            reserve=_caption_height(style,size,2)*1.04
            y=min(round(height*.835-reserve*.5-18),max(round(height*.30+reserve*.5),y))
            if style.plate != 'none':
                events.append(_plate(section_start,section_end,width/2,y,measured_w*1.04,measured_h*1.04,style))
            if style.animation=='rule':
                line=replace(style,plate='banner',plate_colour=style.accents[0])
                events.append(_plate(section_start,section_end,width/2,y+measured_h/2+14,min(measured_w,220),-13,line))
            if style.animation=='word_box':
                box=_word_rectangle(group,keyword,split,style,size,width/2,y)
                events.append(_plate(section_start,section_end,*box,replace(style,plate='rounded',plate_colour=style.accents[0]),pad_x=3,pad_y=0))
            for period_start,period_end,active in periods:
                at,until=max(section_start,period_start),min(section_end,period_end)
                if until-at<.01:
                    continue
                pieces=[]
                for index,item in enumerate(group):
                    text=_escape(item.text.upper() if style.uppercase else item.text)
                    is_active=index==active
                    accent=(style.emphasis=='all' or style.emphasis=='active' and is_active or
                        style.emphasis=='keyword' and index==keyword or
                        style.emphasis=='last_line' and (index>=split if split>0 else index==keyword))
                    if style.animation=='progressive' and index<active:
                        accent=True
                    colour=style.accents[0] if accent else style.primary
                    border=style.outline_size
                    if style.animation=='word_box' and index==keyword:
                        colour='&H00141414'
                        border=0
                    tags=rf"\1c{colour}\u0\alpha&H00&\3c{style.outline}\bord{border}"
                    if accent and style.animation in {'underline','marker'}:
                        tags+=r'\u1'
                    pieces.append('{'+tags+'}'+text)

                lines=[pieces] if split<0 else [pieces[:split],pieces[split:]]
                fade=r'\fad(55,55)' if style.animation in {'phrase_fade','fade_word'} and section_end-section_start>.35 else ''
                for line_index,values in enumerate(lines):
                    ly=round(y+(line_index-(len(lines)-1)/2)*_caption_step(style,size))
                    zoom=_phrase_zoom(start,end,at,until)
                    base=rf"{{\an5\pos({width//2},{ly})\fs{size}\b{style.font_weight}\fsp{style.spacing}\shad{style.shadow}{fade}{zoom}}}"
                    events.append(_dialogue(1,at,until,base+' '.join(values)))
        previous_end=end
    for graphic in ():
        if graphic.kind in {"hook","context_title"}:
            hook_style=replace(style,font='Montserrat',font_weight=700,italic=False,size=52,max_width=820,phrase_mode='balanced',active_peak_scale=100,uppercase=False,spacing=0)
            hook_words=[Word(0,1,word) for word in graphic.text.split()]
            size,split,hook_w,hook_h=_measure(hook_words,hook_style)
            values=[_escape(word.text) for word in hook_words]
            text=" ".join(values) if split<0 else " ".join(values[:split])+r"\N"+" ".join(values[split:])
            hy=1440 if graphic.kind=='context_title' else 210
            plate=_plate(graphic.source_start,graphic.source_end,width/2,hy,hook_w+10,hook_h+10,
                replace(hook_style,plate='rounded',plate_colour='&H00FFFFFF'))
            events.append(plate.replace('Dialogue: 0,','Dialogue: 2,',1))
            events.append(_dialogue(3,graphic.source_start,graphic.source_end,rf"{{\an5\pos({width//2},{hy})\fnMontserrat\fs{size}\b700\i0\1c&H00151515\3c&H00151515\bord0\shad0\fsp0\fad(80,120)}}"+text))
            continue
        value=_escape(graphic.text[:100].upper())
        while _width(value,style,44)>width-280 and len(value)>4:
            value=value.rsplit(" ",1)[0] if " " in value else value[:-1]
        events.append(_dialogue(2,graphic.source_start,graphic.source_end,rf"{{\an5\pos({width//2},300)\fs44\b800\1c&H00FFFFFF\3c&H00101010\bord6\fad(100,100)}}"+value))
    path.write_text(header+"\n".join(events)+"\n",encoding="utf-8")
    return path
