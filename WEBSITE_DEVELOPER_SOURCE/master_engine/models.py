from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str = ""
    audio_codec: str = ""
    rotation: float = 0.0

    @property
    def aspect(self) -> float:
        return self.width / max(1, self.height)


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str
    probability: float = 1.0


@dataclass(frozen=True)
class Transcript:
    words: List[Word]
    text: str
    language: str


@dataclass(frozen=True)
class SourceSpan:
    source_start: float
    source_end: float
    output_start: float

    @property
    def duration(self) -> float:
        return max(0.0, self.source_end - self.source_start)

    @property
    def output_end(self) -> float:
        return self.output_start + self.duration


@dataclass(frozen=True)
class FaceBox:
    x: float
    y: float
    w: float
    h: float
    score: float
    motion: float = 0.0
    track_id: int = -1
    interpolated: bool = False

    @property
    def cx(self) -> float:
        return self.x + self.w * 0.5

    @property
    def cy(self) -> float:
        return self.y + self.h * 0.5


@dataclass
class VisionSample:
    time: float
    faces: List[FaceBox] = field(default_factory=list)
    active_side: Optional[str] = None
    scene_delta: float = 0.0
    edge_density: float = 0.0
    motion_energy: float = 0.0
    speaker_state: str = "ambiguous"
    luma: float = 0.5
    saturation: float = 0.0
    sharpness: float = 0.0
    caption_luma: float = 0.5
    caption_edges: float = 0.0
    dominant_hue: float = 0.0
    caption_contrast: float = 0.0
    active_track_id: int = -1
    speech_confidence: float = 0.0


@dataclass(frozen=True)
class FaceKeyframe:
    time: float
    boxes: Tuple[Tuple[float, float, float, float], ...]


@dataclass(frozen=True)
class LayoutSegment:
    source_start: float
    source_end: float
    mode: str
    center_x: float = 0.5
    center_y: float = 0.5
    top_center_x: float = 0.33
    bottom_center_x: float = 0.67
    active_side: Optional[str] = None
    reason: str = ""
    zoom: float = 1.0
    required_faces: int = 0
    face_centers: Tuple[Tuple[float, float], ...] = ()
    transition: str = "cut"
    face_boxes: Tuple[Tuple[float, float, float, float], ...] = ()
    face_keyframes: Tuple[FaceKeyframe, ...] = ()
    native_cut: bool = False
    turn_seconds: float = 0.0
    motion_profile: str = "smooth"
    caption_y: int = 0
    panel_face_indexes: Tuple[Tuple[int, ...], ...] = ()


@dataclass(frozen=True)
class CaptionStyle:
    name: str
    font: str
    size: int
    min_words: int
    max_words: int
    max_chars: int
    primary: str
    accents: List[str]
    outline: str
    outline_size: int
    shadow: int
    animation: str
    y_normal: int
    y_split: int
    box: bool = False
    uppercase: bool = True
    active_peak_scale: int = 112
    active_settle_scale: int = 104
    normal_scale: int = 100
    lines: int = 2
    family: str = "legacy"
    palette: str = "reference"
    font_weight: int = 700
    spacing: float = 0.0
    italic: bool = False
    plate: str = "none"
    plate_colour: str = "&H30101010"
    max_width: int = 780
    accent_hue: float = 120.0
    energy: float = 0.35
    busy_safe: bool = False
    phrase_mode: str = "balanced"
    emphasis: str = "active"
    description: str = ""


@dataclass(frozen=True)
class EditProfile:
    category: str
    caption_style: str
    layout_policy: str
    pace: str
    silence_cut_seconds: float
    broll_per_minute: float
    broll_seconds: float
    music_mood: str
    sfx_density: float
    transition: str
    overlay_density: float = 0.30
    punch_zoom_density: float = 0.24
    colour_mood: str = "natural"


@dataclass(frozen=True)
class BrollEvent:
    source_start: float
    source_end: float
    query: str
    kind: str
    path: Optional[Path] = None
    credit: str = ""
    source_url: str = ""
    provider: str = "local"
    license: str = "user-owned/licensed"
    relevance: float = 0.0
    transition: str = "fade"


@dataclass(frozen=True)
class StoryBeat:
    start: float
    end: float
    text: str
    kind: str
    importance: float
    keywords: Tuple[str, ...] = ()


@dataclass(frozen=True)
class Chapter:
    start: float
    end: float
    title: str
    beat_indexes: Tuple[int, ...]


@dataclass(frozen=True)
class GraphicEvent:
    source_start: float
    source_end: float
    kind: str
    text: str
    accent: str = "&H0000F15A"
    position: str = "upper"


@dataclass(frozen=True)
class SfxEvent:
    source_time: float
    kind: str
    path: Path
    gain: float = 0.18


@dataclass(frozen=True)
class AudioProfile:
    highpass_hz: int = 70
    lowpass_hz: int = 15000
    noise_reduction_db: int = -25
    target_lufs: float = -14.0
    true_peak_db: float = -1.5
    speech_gain_db: float = 0.0
    music_gain: float = 0.10
    reduction_strength_db: float = 6.0


@dataclass(frozen=True)
class ColourProfile:
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0
    gamma: float = 1.0
    sharpen: float = 0.20
    denoise: float = 0.0
    restoration_mode: str = "native_preserve"


@dataclass
class EditPlan:
    media: MediaInfo
    transcript: Transcript
    profile: EditProfile
    caption_style: CaptionStyle
    spans: List[SourceSpan]
    layouts: List[LayoutSegment]
    broll: List[BrollEvent]
    music_path: Optional[Path]
    output_duration: float
    analysis: Dict[str, Any] = field(default_factory=dict)
    story_beats: List[StoryBeat] = field(default_factory=list)
    chapters: List[Chapter] = field(default_factory=list)
    graphics: List[GraphicEvent] = field(default_factory=list)
    sfx: List[SfxEvent] = field(default_factory=list)
    audio_profile: AudioProfile = field(default_factory=AudioProfile)
    colour_profile: ColourProfile = field(default_factory=ColourProfile)

    def to_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["media"]["path"] = str(self.media.path)
        value["music_path"] = str(self.music_path) if self.music_path else None
        for row in value["broll"]:
            if row.get("path") is not None:
                row["path"] = str(row["path"])
        for row in value["sfx"]:
            row["path"] = str(row["path"])
        return value
