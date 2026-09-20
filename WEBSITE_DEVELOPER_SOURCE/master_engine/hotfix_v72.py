"""Master Editor V7.2: planted framing and wide-podcast subject recovery.

This patch is intentionally additive to the exact V7.1 package.  It keeps the
legacy editors isolated, preserves the requested blur rule, and changes only
Master Editor planning/detection/composition behaviour.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from statistics import median

_installed = False
_frame_family = "9:16"


def _family_for(media) -> str:
    requested = os.getenv("MASTER_FRAME_RATIO", "auto").strip().lower().replace(" ", "")
    aliases = {"9:16": "9:16", "9x16": "9:16", "vertical": "9:16",
               "3:4": "3:4", "3x4": "3:4", "portrait": "3:4"}
    if requested not in {"", "auto"}:
        if requested not in aliases:
            raise ValueError("MASTER_FRAME_RATIO must be auto, 9:16 or 3:4")
        return aliases[requested]
    # Preserve a genuine portrait 3:4 source.  All landscape and phone-vertical
    # sources use the TikTok 9:16 family.  The decision is made once per job.
    return "3:4" if .70 <= media.aspect <= .82 else "9:16"


def _patch_detector():
    from . import vision
    original = vision.FaceDetector.detect

    def detect(self, frame):
        """YuNet full-frame pass plus selective high-confidence landscape tiles.

        Tiles are used only when the normal pass is empty or sees only tiny
        people.  The original score, five-landmark human check and global area
        gate remain mandatory, so a microphone, plant or poster cannot become
        a framing anchor.
        """
        cv = self.cv2
        height, width = frame.shape[:2]
        primary = list(original(self, frame))
        landmarks = dict(getattr(self, "landmarks", {}))
        areas = [w*h/max(1.0, width*height) for _, _, w, h, _ in primary]
        needs_tiles = (self.kind == "yunet" and width >= height*1.15 and
                       (not primary or (len(primary) == 1 and max(areas, default=0) < .018)
                        or (len(primary) <= 2 and max(areas, default=0) < .007)))
        if not needs_tiles:
            self.landmarks = landmarks
            return primary

        candidates = list(primary)
        windows = ((0.00, .58), (.21, .79), (.42, 1.00))
        for left, right in windows:
            x1, x2 = round(width*left), round(width*right)
            patch = frame[:, x1:x2].copy()
            for x, y, w, h, score in original(self, patch):
                row = (x+x1, y, w, h, score)
                coverage = w*h/max(1.0, width*height)
                if coverage < vision.MIN_FACE_AREA or coverage > .50:
                    continue
                candidates.append(row)
                points = getattr(self, "landmarks", {}).get((x, y, w, h), ())
                if points:
                    landmarks[row[:4]] = tuple((px+x1, py) for px, py in points)

        candidates.sort(key=lambda row: (row[4], row[2]*row[3]), reverse=True)
        kept = []
        for row in candidates:
            x, y, w, h, _ = row
            cx, cy = x+w*.5, y+h*.5
            duplicate = False
            for a, b, c, d, _ in kept:
                ix = max(0, min(x+w, a+c)-max(x, a))
                iy = max(0, min(y+h, b+d)-max(y, b))
                overlap = ix*iy/max(1.0, min(w*h, c*d))
                if overlap > .48 or (abs(cx-(a+c*.5)) < min(w, c)*.34 and
                                     abs(cy-(b+d*.5)) < min(h, d)*.34):
                    duplicate = True
                    break
            if not duplicate:
                kept.append(row)
        self.landmarks = {row[:4]: landmarks[row[:4]] for row in kept if row[:4] in landmarks}
        return sorted(kept, key=lambda row: row[0]+row[2]*.5)

    detect.__name__ = "detect"
    detect._master_v72_tiled = True
    vision.FaceDetector.detect = detect


def _patch_composition_and_motion():
    from . import composition as comp
    from . import motion
    canonical_geometry = comp.geometry

    def geometry(mode, active_side=None, face_count=0, panel_members=()):
        # Detector uncertainty must retain the complete sharp source instead of
        # centre-covering a fireplace, plant or microphone.
        requested_mode = "split_reaction" if _frame_family == "3:4" and mode == "diagonal_split" else mode
        geo = canonical_geometry(requested_mode, active_side, face_count, panel_members)
        panels = []
        for panel in geo.panels:
            value = panel
            if mode == "passthrough":
                value = replace(value, face_index=-5, safe=(0, 0, value.width, value.height))
            elif mode == "focus" and face_count:
                value = replace(value, safe=(20, 60, 1040, 1780), face_fraction=.34)
            elif not geo.diagonal and value.face_index >= 0:
                sx, sy, sw, sh = value.safe
                left = min(sx, round(value.width*.045))
                top = min(sy, round(value.height*.045))
                right = max(sx+sw, round(value.width*.955))
                bottom = max(sy+sh, round(value.height*.925))
                value = replace(value, safe=(left, top, right-left, bottom-top))
            panels.append(value)
        geo = replace(geo, panels=tuple(panels))
        if _frame_family != "3:4":
            return geo
        # A 3:4 job keeps the same 1080x1440 framing band for every shot.  The
        # surrounding 9:16 delivery is source-filled by the V7.1 context layer,
        # never black.  Layouts cannot silently switch family mid-video.
        scale, offset = .75, 240
        scaled = []
        for p in geo.panels:
            sx, sy, sw, sh = p.safe
            scaled.append(replace(p, y=offset+round(p.y*scale), height=max(2, round(p.height*scale)),
                safe=(sx, round(sy*scale), sw, max(2, round(sh*scale)))))
        return replace(geo, panels=tuple(scaled), caption_y=offset+round(geo.caption_y*scale), diagonal=False)

    def tracking_envelope(panel, face):
        if panel.members:
            return face
        x, y, w, h = face
        left = max(0.0, x-w*.20)
        top = max(0.0, y-h*.27)
        right = min(1.0, x+w*1.20)
        bottom = min(1.0, y+h*1.62)
        return left, top, right-left, bottom-top

    comp.geometry = geometry
    comp.tracking_envelope = tracking_envelope
    motion.tracking_envelope = tracking_envelope
    motion.camera_path.cache_clear()

    # Several modules intentionally import these hot-path helpers locally.
    from . import shot_director, qa, engine
    shot_director.geometry = geometry
    qa.geometry = geometry
    engine.geometry = geometry


def _patch_director():
    from . import shot_director as director
    from . import vision, engine
    from .composition import validate_geometry
    from .subjects import four_panel_members
    original_runs = director._runs
    original_build = director.build_layouts

    def runs(samples, start, end):
        rows = original_runs(samples, start, end)
        multi = median([sum(1 for face in sample.faces if director.eligible(face))
                        for sample in samples]) >= 2 if samples else False
        if multi:
            # A sub-1.8s mouth-motion winner is a reaction, not permission to
            # ping-pong the camera.  It remains a shared composition.
            for row in rows:
                if row[2] in {"left", "right"} and row[1]-row[0] < 1.80:
                    row[2] = "shared"
            merged = []
            for row in rows:
                if merged and merged[-1][2] == row[2]:
                    merged[-1][1] = row[1]
                else:
                    merged.append(row)
            rows = merged
        return rows

    def build(media, profile, samples, settings, beats=()):
        rows = original_build(media, profile, samples, settings, beats)
        fixed = []
        duo_modes = {"duo_context", "speaker_context", "split_reaction",
                     "horizontal_split", "diagonal_split", "cinema_duo", "offset_duo"}
        for row in rows:
            count = len(row.face_boxes)
            seconds = row.source_end-row.source_start
            mode, side, members = row.mode, row.active_side, row.panel_face_indexes
            if count == 0:
                mode, side, members = "passthrough", None, ()
            elif count == 1:
                mode = "passthrough" if .50 <= media.aspect <= .82 else "focus"
                side, members = None, ()
            elif count == 2:
                members = ()
                if seconds <= 2.001:
                    mode, side = "fit_blur", None
                else:
                    if mode not in duo_modes:
                        mode = "duo_context"
                    if _frame_family == "3:4" and mode == "diagonal_split":
                        mode = "split_reaction"
            elif count == 3:
                mode, side, members = "grid3", None, ()
            elif count == 4:
                mode, side, members = "grid4", None, ()
            else:
                mode, side = "grid4_context", None
                members = four_panel_members(row.face_boxes, media.aspect)
            reason = row.reason
            if count == 0:
                reason = "no verified human anchor; complete sharp source retained over non-black context"
            elif mode == "fit_blur":
                reason = "two or more verified people share a source shot for <=2.00 seconds"
            updated = replace(row, mode=mode, active_side=side, panel_face_indexes=members,
                              required_faces=count, reason=reason)
            errors = validate_geometry(media, updated)
            if errors:
                fallback = "grid4_context" if count > 4 else "grid4" if count == 4 else \
                           "grid3" if count == 3 else "duo_context" if count == 2 else \
                           "focus" if count == 1 else "passthrough"
                members = four_panel_members(row.face_boxes, media.aspect) if fallback == "grid4_context" else ()
                updated = replace(updated, mode=fallback, active_side=None,
                                  panel_face_indexes=members,
                                  reason="V7.2 geometry-safe local fallback: "+"; ".join(errors))
            fixed.append(updated)
        return fixed

    director._runs = runs
    director.build_layouts = build
    vision.build_layouts = build
    engine.build_layouts = build


def _patch_engine_metadata():
    from . import engine
    original = engine.MasterEngine._build_plan

    def build(self, input_path, *args, **kwargs):
        global _frame_family
        media = engine.probe(Path(input_path))
        _frame_family = _family_for(media)
        plan = original(self, input_path, *args, **kwargs)
        empty = sum(row.source_end-row.source_start for row in plan.layouts
                    if not row.face_boxes)
        plan.analysis.update({
            "engine_version": "7.2.0",
            "frame_ratio_family": _frame_family,
            "frame_ratio_locked_for_entire_video": True,
            "ratio_switches": 0,
            "camera_policy": "planted dead-zone; correction begins before the first protected-boundary crossing",
            "wide_podcast_detector": "full-frame YuNet plus selective overlapping high-confidence tiles",
            "object_anchor_allowed": False,
            "uncertain_full_source_seconds": round(empty, 3),
            "blur_policy": "only 2+ verified people in one source shot lasting <=2.00 seconds",
            "short_turn_policy": "sub-1.8s multi-person mouth changes stay in one shared frame",
        })
        return plan

    engine.MasterEngine._build_plan = build
    original_job = engine.MasterEngine.run_job

    def run_job(self, *args, **kwargs):
        result = original_job(self, *args, **kwargs)
        report = Path(result.get("report", ""))
        if report.is_file():
            data = json.loads(report.read_text(encoding="utf-8"))
            data["engine"] = "Master Editor 7.2.0"
            engine.write_json(report, data)
        return result

    engine.MasterEngine.run_job = run_job


def install():
    global _installed
    if _installed:
        return
    _installed = True
    _patch_detector()
    _patch_composition_and_motion()
    _patch_director()
    _patch_engine_metadata()
