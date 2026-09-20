from __future__ import annotations

import shutil
import math
from statistics import median
from typing import Sequence

from .models import AudioProfile, ColourProfile, MediaInfo, VisionSample


def audio_profile(energy: Sequence[float], enabled: bool = True) -> AudioProfile:
    if not enabled or not energy:
        return AudioProfile(noise_reduction_db=-80, speech_gain_db=0.0)
    ordered = sorted(float(value) for value in energy)
    floor = ordered[max(0, int(len(ordered) * .18) - 1)]
    speech = ordered[max(0, int(len(ordered) * .72) - 1)]
    gain = 0.0
    if speech < .035:
        gain = min(5.0, max(0.0, (.035 - speech) * 120.0))
    reduction = max(-70,min(-32,20*math.log10(max(.0001,floor))))
    # Limited reduction preserves breath/consonants instead of metallic speech.
    strength=8.0 if floor>.009 else 5.0
    return AudioProfile(
        highpass_hz=68,
        lowpass_hz=15500,
        noise_reduction_db=reduction,
        target_lufs=-14.0,
        true_peak_db=-1.5,
        speech_gain_db=round(gain, 2),
        music_gain=0.0,
        reduction_strength_db=strength,
    )


def quality_assessment(media: MediaInfo, samples: Sequence[VisionSample]) -> dict:
    sharpness=median(row.sharpness for row in samples) if samples else None
    short_edge=min(media.width,media.height)
    low_resolution=short_edge<720
    soft_source=sharpness is not None and sharpness<.14
    restore=low_resolution or soft_source
    return {
        "short_edge_pixels":short_edge,
        "median_normalized_sharpness":None if sharpness is None else round(sharpness,3),
        "low_resolution":low_resolution,
        "soft_source":soft_source,
        "restoration_applied":restore,
        "mode":"adaptive_restore" if restore else "native_detail_preserved",
        "method":("mild temporal/spatial denoise + edge-limited sharpen + Lanczos scaling"
                  if restore else "measured colour balance + light edge-limited sharpen"),
    }


def colour_profile(media: MediaInfo, samples: Sequence[VisionSample], enabled: bool = True) -> ColourProfile:
    assessment=quality_assessment(media,samples)
    if not enabled:
        return ColourProfile(sharpen=0.0,denoise=0.0,restoration_mode="disabled")
    if not samples:
        return ColourProfile(sharpen=.30 if assessment["low_resolution"] else .12,
            denoise=.80 if assessment["low_resolution"] else 0.0,
            restoration_mode=str(assessment["mode"]))
    luma = median(row.luma for row in samples)
    saturation = median(row.saturation for row in samples)
    sharpness = median(row.sharpness for row in samples)
    brightness = max(-.035, min(.055, (.48 - luma) * .12))
    contrast = max(1.0, min(1.10, 1.04 + (.42 - abs(.50 - luma)) * .04))
    sat = max(.94, min(1.16, 1.08 + (.22 - saturation) * .18))
    restore=bool(assessment["restoration_applied"])
    sharpen = .34 if restore else (.20 if sharpness < .38 else .10)
    return ColourProfile(
        brightness=round(brightness, 4),
        contrast=round(contrast, 4),
        saturation=round(sat, 4),
        gamma=1.0,
        sharpen=round(sharpen, 3),
        denoise=.90 if restore else 0.0,
        restoration_mode=str(assessment["mode"]),
    )


def audio_filter(profile: AudioProfile) -> str:
    filters = [
        f"highpass=f={profile.highpass_hz}",
        f"lowpass=f={profile.lowpass_hz}",
    ]
    if profile.noise_reduction_db > -70:
        filters.append(f"afftdn=nf={profile.noise_reduction_db:.2f}:nr={profile.reduction_strength_db:.2f}:tn=1")
    if abs(profile.speech_gain_db) >= .05:
        filters.append(f"volume={profile.speech_gain_db:+.2f}dB")
    filters.extend([
        "acompressor=threshold=0.12:ratio=2:attack=12:release=180",
        f"loudnorm=I={profile.target_lufs}:TP={profile.true_peak_db}:LRA=11",
    ])
    return ",".join(filters)


def colour_filter(profile: ColourProfile) -> str:
    filters = []
    if profile.denoise > .01:
        amount=profile.denoise
        # hqdn3d is substantially faster than neural upscalers on this CPU VPS
        # and is available in FFmpeg 4.4.  Values stay mild so faces and text do
        # not acquire the plastic look common to unconditional enhancement.
        filters.append(
            f"hqdn3d={amount:.2f}:{amount*.80:.2f}:{amount*2.50:.2f}:{amount*2.00:.2f}"
        )
    filters.append(
        "eq="
        f"brightness={profile.brightness:.4f}:"
        f"contrast={profile.contrast:.4f}:"
        f"saturation={profile.saturation:.4f}:"
        f"gamma={profile.gamma:.4f}"
    )
    if profile.sharpen > .01:
        filters.append(f"unsharp=5:5:{profile.sharpen:.3f}:5:5:0")
    return ",".join(filters)


def capability_report() -> dict[str, object]:
    return {
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "ffprobe": bool(shutil.which("ffprobe")),
        "measured_audio_polish": True,
        "measured_colour_polish": True,
        "conditional_quality_restoration": True,
    }
