from __future__ import annotations

import math
import json
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .captions import write_ass
from .config import Settings
from .models import EditPlan, GraphicEvent, LayoutSegment, SourceSpan
from .polish import audio_filter, colour_filter
from .render_runtime import FFmpegRuntime
from .timeline import map_words
from .utils import CommandError, emit, filter_escape, run


@dataclass(frozen=True)
class RenderSlice:
    source_start: float
    source_end: float
    output_start: float
    output_end: float
    layout: LayoutSegment

    @property
    def duration(self) -> float:
        return self.output_end - self.output_start


def _slice_frames(row: RenderSlice, fps: float) -> int:
    # Quantise GLOBAL boundaries, not each duration independently. This keeps
    # hundreds of 29.97/23.976-FPS cuts from accumulating caption/audio drift.
    return max(1, round(row.output_end*fps)-round(row.output_start*fps))


def _at_layout(layouts: Sequence[LayoutSegment], time_value: float) -> LayoutSegment:
    for row in layouts:
        if row.source_start - 1e-6 <= time_value <= row.source_end + 1e-6:
            return row
    return layouts[-1]


def _build_slices(plan: EditPlan) -> List[RenderSlice]:
    result: List[RenderSlice] = []
    for span in plan.spans:
        points = {span.source_start, span.source_end}
        for layout in plan.layouts:
            if span.source_start < layout.source_start < span.source_end:
                points.add(layout.source_start)
            if span.source_start < layout.source_end < span.source_end:
                points.add(layout.source_end)
        ordered = sorted(points)
        for start, end in zip(ordered, ordered[1:]):
            if end - start < 0.025:
                continue
            midpoint = (start + end) * 0.5
            output_start = span.output_start + start - span.source_start
            output_end = output_start + end - start
            result.append(
                RenderSlice(
                    start,
                    end,
                    output_start,
                    output_end,
                    _at_layout(plan.layouts, midpoint),
                )
            )
    fps=float(plan.analysis.get("render_fps") or plan.analysis.get("settings_fps") or 30)
    minimum=max(.16,4.0/max(12.0,fps))
    # Eliminate one-frame layout slivers before FFmpeg.  They can be created by
    # the intersection of a valid silence cut and a valid camera boundary even
    # when both parent plans are stable.  Merge only across continuous source
    # time, so no removed pause is accidentally restored.
    index=0
    while index<len(result):
        row=result[index]
        if row.duration>=minimum-.001:
            index+=1
            continue
        previous=result[index-1] if index else None
        following=result[index+1] if index+1<len(result) else None
        if (previous and abs(previous.source_end-row.source_start)<.003
                and abs(previous.output_end-row.output_start)<.003):
            layout=replace(previous.layout,source_end=row.source_end)
            result[index-1]=RenderSlice(previous.source_start,row.source_end,
                previous.output_start,row.output_end,layout)
            result.pop(index)
            continue
        if (following and abs(row.source_end-following.source_start)<.003
                and abs(row.output_end-following.output_start)<.003):
            layout=replace(following.layout,source_start=row.source_start)
            result[index+1]=RenderSlice(row.source_start,following.source_end,
                row.output_start,following.output_end,layout)
            result.pop(index)
            continue
        # This should be unreachable after build_spans' retained-fragment
        # guard. Refuse a risky native graph rather than sending a broken file.
        raise CommandError(
            f"Unsafe isolated render fragment {row.output_start:.3f}-"
            f"{row.output_end:.3f}s could not be merged"
        )
    return result


def _chunk_slices(slices: Sequence[RenderSlice], seconds: float) -> List[List[RenderSlice]]:
    if not slices:
        return []
    split: List[RenderSlice] = []
    for row in slices:
        source_cursor = row.source_start
        output_cursor = row.output_start
        while output_cursor < row.output_end - 1e-6:
            boundary = (math.floor(output_cursor / seconds) + 1) * seconds
            output_end = min(row.output_end, boundary)
            length = output_end - output_cursor
            split.append(
                RenderSlice(
                    source_cursor,
                    source_cursor + length,
                    output_cursor,
                    output_end,
                    row.layout,
                )
            )
            source_cursor += length
            output_cursor = output_end
    groups: List[List[RenderSlice]] = []
    current: List[RenderSlice] = []
    current_index = None
    for row in split:
        index = int((row.output_start + 1e-5) // seconds)
        if current and index != current_index:
            groups.append(current)
            current = []
        current_index = index
        current.append(row)
    if current:
        groups.append(current)
    return groups


def _source_transform(label: str, output: str, layout: LayoutSegment, plan: EditPlan, index: int, source_time=None,source_end=None) -> List[str]:
    from .composition import render_geometry
    fps = float(plan.analysis.get("render_fps") or plan.analysis.get("settings_fps") or 30)
    return render_geometry(label, output, layout, plan.media, fps, index, source_time,source_end)


def _render_chunk(
    rows: Sequence[RenderSlice],
    plan: EditPlan,
    settings: Settings,
    target: Path,
    *,
    runtime: FFmpegRuntime,
    cancel_check=None,
) -> None:
    earliest = min(row.source_start for row in rows)
    latest = max(row.source_end for row in rows)
    render_fps = float(plan.analysis.get("render_fps") or settings.fps)
    args = [
        "-ss", f"{earliest:.6f}", "-t", f"{max(0.05, latest-earliest):.6f}",
        "-i", str(plan.media.path),
    ]
    # Normalise once, before crop/split; do not allow individual overlays to
    # negotiate different high-bit-depth/RGB formats through the graph.
    filter_lines: List[str] = ["[0:v:0]format=pix_fmts=yuv420p[vsource]"]
    source_video_indexes = list(range(len(rows)))
    if len(source_video_indexes) > 1:
        labels = "".join(f"[svraw{index}]" for index in source_video_indexes)
        filter_lines.append(f"[vsource]split={len(source_video_indexes)}{labels}")
    elif len(source_video_indexes) == 1:
        filter_lines.append(f"[vsource]null[svraw{source_video_indexes[0]}]")
    if plan.media.has_audio:
        if len(rows) > 1:
            filter_lines.append("[0:a]asplit=" + str(len(rows)) + "".join(f"[araw{i}]" for i in range(len(rows))))
        else:
            filter_lines.append("[0:a]anull[araw0]")

    for index, row in enumerate(rows):
        duration = row.duration
        frame_count = _slice_frames(row, render_fps)
        video_duration = frame_count/render_fps
        start = row.source_start - earliest
        end = row.source_end - earliest
        filter_lines.append(
            f"[svraw{index}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS[vtrim{index}]"
        )
        filter_lines.extend(_source_transform(f"vtrim{index}", f"vraw{index}", row.layout, plan, index,row.source_start,row.source_end))
        # fps now precedes composition, so it no longer discards the final
        # overlaid frame. No clone-padding: FFmpeg 4.4 can crash on empty input.
        filter_lines.append(f"[vraw{index}]trim=end_frame={frame_count},setpts=N/({render_fps}*TB)[vseg{index}]")
        if plan.media.has_audio:
            start = row.source_start - earliest
            end = row.source_end - earliest
            filter_lines.append(
                f"[araw{index}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,apad=pad_dur=0.12,atrim=duration={video_duration:.9f}[aseg{index}]"
            )
    if len(rows) == 1:
        filter_lines.append("[vseg0]null[vout]")
        if plan.media.has_audio:
            filter_lines.append("[aseg0]anull[aout]")
    else:
        filter_lines.append("".join(f"[vseg{i}]" for i in range(len(rows))) + f"concat=n={len(rows)}:v=1:a=0[vout]")
        if plan.media.has_audio:
            filter_lines.append("".join(f"[aseg{i}]" for i in range(len(rows))) + f"concat=n={len(rows)}:v=0:a=1[aout]")
    script = target.with_suffix(".filters.txt")
    script.write_text(";\n".join(filter_lines) + "\n", encoding="utf-8")
    args.extend([
        "-filter_complex_script", str(script),
        "-map", "[vout]",
    ])
    if plan.media.has_audio:
        args.extend(["-map", "[aout]"])
    args.extend([
        "-c:v", "libx264", "-preset", settings.preset, "-crf", "14",
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(render_fps),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ])
    if plan.media.has_audio:
        # Lossless intermediate audio avoids AAC priming drift at every chunk.
        args.extend(["-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2"])
    args.extend(["-t",f"{sum(_slice_frames(row,render_fps) for row in rows)/render_fps:.9f}",str(target)])
    try:
        runtime.execute(
            lambda: runtime.prefix() + runtime.input_args() + args[:-1] + runtime.encoder_args() + args[-1:],
            stage=f"shot {rows[0].output_start:.3f}-{rows[-1].output_end:.3f}s",
            script=script, timeout=max(900, sum(row.duration for row in rows) * 24),
        )
        # Exit status zero is not proof of any encoded frames. Check exact
        # frame count before concatenation; never accept an empty/short shot.
        def inspect_chunk(path):
            probe = run([
                "ffprobe", "-v", "error", "-threads", "1", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=width,height,nb_read_frames", "-of", "json", str(path),
            ], timeout=max(60, sum(row.duration for row in rows)*4), cancel_check=cancel_check)
            streams = json.loads(probe.stdout).get("streams", [])
            if (len(streams)!=1 or streams[0].get("width")!=settings.width
                    or streams[0].get("height")!=settings.height):
                raise CommandError(f"Shot dimensions/stream check failed: {streams}")
            return int(streams[0].get("nb_read_frames") or 0)

        expected = sum(_slice_frames(row, render_fps) for row in rows)
        actual = inspect_chunk(target)
        if actual>0 and actual==expected-1:
            # Fractional-FPS global boundaries can require one final hold frame.
            # Only repeat a DECODE-VERIFIED last frame; never pad an empty stream.
            corrected = target.with_suffix(".rounding.nut")
            correction = ["-i", str(target), "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", f"loop=loop=1:size=1:start={actual-1},setpts=N/({render_fps}*TB)",
                "-c:v", "libx264", "-preset", settings.preset, "-crf", "14",
                "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(render_fps),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                "-c:a", "copy", "-t", f"{expected/render_fps:.9f}"]
            try:
                runtime.execute(lambda: runtime.prefix()+runtime.input_args()+correction+
                    runtime.encoder_args()+[str(corrected)],
                    stage=f"one-frame rounding completion at {rows[0].output_start:.3f}s",
                    timeout=max(90, sum(row.duration for row in rows)*24))
                actual = inspect_chunk(corrected)
                if actual!=expected:
                    raise CommandError(f"Rounding completion returned {actual}, expected {expected} frames")
                corrected.replace(target)
            finally:
                corrected.unlink(missing_ok=True)
        if actual!=expected:
            raise CommandError(f"Shot frame-count check failed: expected {expected} frames; got {actual}")
    finally:
        script.unlink(missing_ok=True)


def _map_layouts_to_output(layouts: Sequence[LayoutSegment], spans: Sequence[SourceSpan]) -> List[LayoutSegment]:
    result: List[LayoutSegment] = []
    for span in spans:
        for row in layouts:
            start = max(span.source_start, row.source_start)
            end = min(span.source_end, row.source_end)
            if end <= start:
                continue
            out_start = span.output_start + start - span.source_start
            out_end = out_start + end - start
            result.append(replace(row, source_start=out_start, source_end=out_end))
    return result


def _map_graphics_to_output(
    graphics: Sequence[GraphicEvent],
    spans: Sequence[SourceSpan],
) -> List[GraphicEvent]:
    result: List[GraphicEvent] = []
    for graphic in graphics:
        for span in spans:
            start = max(span.source_start, graphic.source_start)
            end = min(span.source_end, graphic.source_end)
            if end <= start:
                continue
            out_start = span.output_start + start - span.source_start
            result.append(replace(
                graphic,
                source_start=out_start,
                source_end=out_start + end - start,
            ))
    return result


def _concat_chunks(chunks: Sequence[Path], joined: Path, *, durations: Sequence[float],
                   runtime: FFmpegRuntime, cancel_check=None) -> None:
    if len(chunks) != len(durations) or any(not math.isfinite(value) or value <= 0 for value in durations):
        raise CommandError("Invalid quantised chunk timing")
    if len(chunks) == 1:
        shutil.copy2(chunks[0], joined)
        return
    manifest = joined.with_suffix(".concat.txt")
    manifest.write_text(
        "ffconcat version 1.0\n" + "\n".join(
            "file '" + str(path.resolve()).replace("'", "'\\''") + "'\n" + f"duration {duration:.12f}"
            for path, duration in zip(chunks, durations)) + "\n",
        encoding="utf-8",
    )
    try:
        runtime.execute(
            lambda: runtime.prefix() + ["-f", "concat", "-safe", "0", "-i", str(manifest), "-c", "copy", str(joined)],
            stage="lossless chunk concatenation", script=manifest,
            timeout=max(600, len(chunks) * 120),
        )
    finally:
        manifest.unlink(missing_ok=True)


def _final_pass(
    joined: Path,
    ass_path: Optional[Path],
    plan: EditPlan,
    output: Path,
    settings: Settings,
    *,
    runtime: FFmpegRuntime,
    cancel_check=None,
) -> None:
    duration = plan.output_duration
    has_audio = plan.media.has_audio
    render_fps = float(plan.analysis.get("render_fps") or settings.fps)
    args = ["-i",str(joined)]
    video_filter="format=pix_fmts=yuv420p,"+colour_filter(plan.colour_profile)
    if ass_path and ass_path.exists() and ass_path.stat().st_size>0:
        from .fonts import FONT_DIR
        video_filter+=f",subtitles='{filter_escape(ass_path)}':fontsdir='{filter_escape(FONT_DIR)}'"
    filter_lines=[f"[0:v]{video_filter}[vout]"]
    have_base=has_audio
    if has_audio:
        filter_lines.append(f"[0:a]{audio_filter(plan.audio_profile)},alimiter=limit=0.96[aout]")
    filter_path = output.with_suffix(".final_filters.txt")
    filter_path.write_text(";\n".join(filter_lines) + "\n", encoding="utf-8")
    args.extend(["-filter_complex_script", str(filter_path), "-map", "[vout]"])
    if have_base:
        args.extend(["-map", "[aout]"])
    args.extend([
        "-c:v", "libx264", "-preset", settings.preset, "-crf", str(settings.crf),
        "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(render_fps),
        "-g", str(max(24, round(render_fps * 2))), "-keyint_min", str(max(12, round(render_fps))),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
    ])
    if have_base:
        args.extend(["-c:a", "aac", "-b:a", settings.audio_bitrate, "-ar", "48000"])
    args.extend(["-movflags", "+faststart", "-t", f"{duration:.6f}", str(output)])
    try:
        runtime.execute(
            lambda: runtime.prefix() + runtime.input_args() + args[:-1] + runtime.encoder_args() + args[-1:],
            stage=("captioned delivery finishing" if ass_path else "caption-free delivery finishing"), script=filter_path,
            timeout=max(1200, duration * 26),
        )
    finally:
        filter_path.unlink(missing_ok=True)


def _render_body(
    plan: EditPlan,
    output: Path,
    work: Path,
    settings: Settings,
    *,
    runtime: FFmpegRuntime,
    clean_output: Optional[Path] = None,
    progress=None,
    cancel_check=None,
) -> Tuple[Path, Path]:
    output.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    # Hard requirement, not a preference/environment toggle. V5 adds no music,
    # sound effects, stock clips or placeholder B-roll, including direct callers.
    plan.music_path=None
    plan.broll=[]
    plan.sfx=[]
    slices = _build_slices(plan)
    # Only ONE planned composition is resident per graph. Splitting many
    # separate trim/scale/blur/overlay branches in one process can hold frames
    # for the whole chunk and multiply a VPS's peak working set.
    groups = [[row] for group in _chunk_slices(slices, settings.chunk_seconds) for row in group]
    if not groups:
        raise CommandError("Director produced an empty render timeline")
    chunks: List[Path] = []
    chunk_durations: List[float] = []
    fps = float(plan.analysis.get("render_fps") or settings.fps)
    for index, rows in enumerate(groups, 1):
        emit(progress, "render", (index - 1) / len(groups), f"Rendering controlled shot chunk {index}/{len(groups)}")
        target = work / f"chunk_{index:04d}.nut"
        _render_chunk(rows, plan, settings, target, runtime=runtime, cancel_check=cancel_check)
        chunks.append(target)
        chunk_durations.append(sum(_slice_frames(row, fps) for row in rows) / fps)
    joined = work / "joined_master.nut"
    # NUT container duration may end at a last packet's PTS rather than its end.
    # Explicit frame-quantised durations prevent that error accumulating over
    # many short independently rendered shots (especially fractional FPS).
    _concat_chunks(chunks, joined, durations=chunk_durations, runtime=runtime, cancel_check=cancel_check)
    mapped_words = map_words(plan.transcript.words, plan.spans) if settings.captions_enabled else []
    output_layouts = _map_layouts_to_output(plan.layouts, plan.spans)
    output_graphics = _map_graphics_to_output(plan.graphics, plan.spans)
    ass_path = work / "master_captions.ass"
    if mapped_words:
        write_ass(
            mapped_words,
            output_layouts,
            plan.caption_style,
            ass_path,
            width=settings.width,
            height=settings.height,
            graphics=output_graphics,
        )
    elif output_graphics:
        write_ass(
            [], output_layouts, plan.caption_style, ass_path,
            width=settings.width, height=settings.height, graphics=output_graphics,
        )
    else:
        ass_path.write_text("", encoding="utf-8")
    emit(progress, "render", 0.92, "Finishing automatic captions, colour and clean source dialogue")
    _final_pass(
        joined,
        ass_path if mapped_words or output_graphics else None,
        plan,
        output,
        settings,
        runtime=runtime,
        cancel_check=cancel_check,
    )
    if clean_output is not None:
        if clean_output.resolve()==output.resolve():
            raise CommandError("Caption-free output path must differ from the captioned output")
        clean_output.parent.mkdir(parents=True,exist_ok=True)
        emit(progress,"render",.96,"Preparing the post-edit Remove Captions version from the identical edit plan")
        _final_pass(joined,None,plan,clean_output,settings,runtime=runtime,cancel_check=cancel_check)
    # An encoder/muxer exit status of zero is not a structural decode check.
    # Decode every produced packet before handing the MP4 to QA/delivery;
    # -xerror fails on corrupt video/audio instead of silently skipping it.
    for candidate,label in ((output,"captioned"),(clean_output,"caption-free")):
        if candidate is None:
            continue
        runtime.execute(
            lambda candidate=candidate: runtime.prefix() + ["-v", "error", "-xerror"] + runtime.input_args() + [
                "-i", str(candidate), "-map", "0:v:0", "-map", "0:a:0?",
                "-threads:v", "1", "-threads:a", "1", "-f", "null", "-",
            ],
            stage=f"complete {label} video/audio integrity check",
            timeout=max(300, plan.output_duration * 4),
        )
    emit(progress, "render", 1.0, "Master render complete")
    return output, ass_path


def render(plan: EditPlan, output: Path, work: Path, settings: Settings, *,
           clean_output: Optional[Path] = None, progress=None,
           cancel_check=None) -> Tuple[Path, Path]:
    safe = settings.ffmpeg_compatibility or plan.analysis.get("ffmpeg_runtime", {}).get("compatibility_cpu", False)
    with FFmpegRuntime(plan, compatibility=safe,
                       progress=progress, cancel_check=cancel_check) as runtime:
        return _render_body(plan, output, work, settings, runtime=runtime,
                            clean_output=clean_output,
                            progress=progress, cancel_check=cancel_check)
