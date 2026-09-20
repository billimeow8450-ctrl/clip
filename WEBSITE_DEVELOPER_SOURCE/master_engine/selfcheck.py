"""Small deterministic safety checks shipped inside the standalone installer."""
from dataclasses import asdict
import json
from pathlib import Path

from .caption_catalog import catalog
from .composition import LAYOUT_NAMES, PRODUCTION_LAYOUTS, validate_geometry
from .models import LayoutSegment, MediaInfo


def check_design() -> dict:
    styles = catalog()
    if len(styles) != 192:
        raise ValueError("The caption catalogue must contain 192 presets")
    signatures = set()
    for style in styles.values():
        data = asdict(style)
        for key in ("name", "family", "palette"):
            data.pop(key, None)
        signatures.add(json.dumps(data, sort_keys=True))
        if style.lines != 2 or style.active_peak_scale > 104:
            raise ValueError("Uncontrolled caption layout or motion")
    if len(signatures) != 192:
        raise ValueError("Duplicate caption configurations")
    media = MediaInfo(Path("design-check.mp4"), 8.0, 1920, 1080, 29.97, False)
    boxes = ((.05,.24,.18,.35),(.30,.28,.14,.28),(.53,.24,.18,.35),(.80,.29,.14,.30))
    checks = 0
    counts={"focus":1,"passthrough":1,"solo_medium":1,"portrait_card":1,
            "speaker_detail":1,"fit_blur":2,"grid3":3,"triple_column":3,
            "hero_strip":3,"grid4":4,"grid4_context":6}
    crowd=tuple((.04+(n%3)*.31,.14+(n//3)*.42,.11,.18) for n in range(6))
    for name in LAYOUT_NAMES:
        count=counts.get(name,2)
        for active in ("left", "right"):
            face_boxes=crowd if name=="grid4_context" else boxes[:count]
            members=()
            if name=="grid4_context":
                from .subjects import four_panel_members
                members=four_panel_members(face_boxes,media.aspect)
            row = LayoutSegment(0, min(2,8) if name=="fit_blur" else 8, name,
                active_side=active, required_faces=count, face_boxes=face_boxes,
                panel_face_indexes=members)
            errors = validate_geometry(media, row)
            if errors:
                raise ValueError(f"{name}: {'; '.join(errors)}")
            checks += 1
    from .fonts import check_fonts
    fonts=check_fonts()
    crowd_checks=0
    if "group_context" in LAYOUT_NAMES or "group_context" in PRODUCTION_LAYOUTS:
        raise ValueError("Removed room-wide group frame is still registered")
    for count in (3,4,5,8,12,20):
        crowd=tuple((.04+(n%5)*.18,.06+(n//5)*.22,.08,.13) for n in range(count))
        from .subjects import four_panel_members
        groups=four_panel_members(crowd,media.aspect)
        row=LayoutSegment(0,8,'grid4_context',required_faces=count,face_boxes=crowd,
                          panel_face_indexes=groups)
        if set(i for group in groups for i in group)!=set(range(count)) or validate_geometry(media,row):
            raise ValueError('Four-panel crowd coverage failed')
        crowd_checks+=1
    from .motion import profile_values
    profile_values("xml_reference")
    return {"unique_caption_presets": len(signatures), "layout_geometry_cases": checks,
        "caption_families":len({row.family for row in styles.values()}),"bundled_fonts":fonts,
        "xml_motion_preset":True,"added_music":False,"added_broll":False,
        "crowd_geometry_cases":crowd_checks,"crowd_panel_count":4,"four_face_tracking_cap":False,
        "automatic_layouts":list(PRODUCTION_LAYOUTS),"removed_wide_group_frame":True,
        "short_multi_speaker_blur_limit_seconds":2.0}
