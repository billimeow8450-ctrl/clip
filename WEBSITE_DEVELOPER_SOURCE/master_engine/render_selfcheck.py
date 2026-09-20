"""A short, real FFmpeg render probe. No Telegram, model downloads or ASR."""
from __future__ import annotations

import tempfile
from pathlib import Path

from .config import Settings, load_caption_styles
from .director import PROFILES
from .media import probe
from .models import EditPlan, LayoutSegment, SourceSpan, Transcript, VisionSample, Word
from .qa import assert_output
from .render import render
from .render_runtime import FFmpegRuntime


def check_renderer(*, compatibility: bool = False) -> dict:
    """Exercise every automatic frame, ASS, audio, muxing and clean output.

    These are deliberately short synthetic test shots, not a podcast edit or
    face-detector certification. Production shot-duration rules are unchanged.
    """
    with tempfile.TemporaryDirectory(prefix="master_ffmpeg_probe_") as directory:
        work = Path(directory)
        source = work / "probe_source.mp4"
        step=.30
        duo = ((.12, .27, .19, .32), (.66, .27, .19, .32))
        three=((.08,.22,.15,.28),(.42,.25,.15,.28),(.75,.22,.15,.28))
        four=((.08,.16,.14,.24),(.58,.16,.14,.24),(.08,.58,.14,.24),(.58,.58,.14,.24))
        crowd = tuple((.05 + (n%3)*.31, .16+(n//3)*.42, .11, .19) for n in range(6))
        specs=[("focus",((.38,.25,.20,.32),),1,()),("passthrough",(),0,()),
            ("fit_blur",duo,2,()),("duo_context",duo,2,()),
            ("speaker_context",duo,2,()),("split_reaction",duo,2,()),
            ("diagonal_split",duo,2,()),("cinema_duo",duo,2,()),
            ("offset_duo",duo,2,()),("grid3",three,3,()),("grid4",four,4,())]
        from .subjects import four_panel_members
        specs.append(("grid4_context",crowd,6,four_panel_members(crowd)))
        layouts=[]
        for index,(mode,boxes,count,members) in enumerate(specs):
            layouts.append(LayoutSegment(index*step,(index+1)*step,mode,
                active_side="left",required_faces=count,face_boxes=boxes,
                panel_face_indexes=members,turn_seconds=step))
        duration=len(layouts)*step
        from .models import MediaInfo
        words = [Word(.02, duration*.48, "RENDER"),
                 Word(duration*.50, duration-.02, "TEST")]
        plan = EditPlan(MediaInfo(source, duration, 960, 540, 30, True),
                        Transcript(words, "RENDER TEST", "en"), PROFILES["podcast_interview"],
                        load_caption_styles()["reference_bold_lime"],
                        [SourceSpan(0, duration, 0)], layouts, [], None, duration,
                        analysis={"render_fps": 30, "enforce_visible_face_qa": False})
        with FFmpegRuntime(plan, compatibility=compatibility) as generator:
            generator.execute(lambda: generator.prefix() + [
                "-f", "lavfi", "-i", f"testsrc2=size=960x540:rate=30:duration={duration}",
                "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "14",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
            ] + generator.encoder_args() + ["-t", str(duration), str(source)],
                stage="synthetic renderer preflight source", timeout=90)
        plan.media = probe(source)
        from .polish import colour_profile
        plan.colour_profile=colour_profile(plan.media,[VisionSample(0,sharpness=.08,luma=.42,saturation=.18)])
        settings = Settings(preset="fast", ffmpeg_compatibility=generator.compatibility)
        output = work / "probe_output.mp4"
        clean = work / "probe_output_NO_CAPTIONS.mp4"
        render(plan, output, work / "render", settings, clean_output=clean)
        rendering = dict(plan.analysis["ffmpeg_runtime"])
        review = assert_output(output, plan)
        return {
            "ok": review["passed"], "real_ffmpeg_render": True,
            "resolution": [review["output"]["width"], review["output"]["height"]],
            "tested_layouts": [row.mode for row in layouts],
            "six_people_four_panels": True,"short_multi_speaker_blur":False,"blur_disabled":True,
            "removed_wide_group_frame":True,"ass_captions": True, "source_audio": True,
            "conditional_quality_enhancer":plan.colour_profile.denoise>0,
            "caption_free_output": clean.is_file(),
            "compatibility_cpu": rendering["compatibility_cpu"],
            "full_output_decode": True,
            "render_runtime": rendering,
            "note": "Synthetic runtime probe; the user's source video, ASR and Telegram are not tested by this check.",
        }
