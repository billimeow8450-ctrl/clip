# Master Editor 6.1.0

The V6 menu and its two reversible hook lines remain unchanged. No legacy AI
Clipper or AI Editor function is imported or edited.

- Rotation: source dimensions are read in display orientation, matching the
  pixels OpenCV analyzes and FFmpeg crops. Regression fixtures include quarter
  turns in both directions and 180-degree orientation.
- Temporary face loss: interpolate a short, bounded gap only between two
  observations of the same track, within the same native camera shot. Do not
  extrapolate a departed person or treat interpolation as mouth/speech evidence.
- Unstable participant counts: locally preserve everyone in a shared view,
  respect the layout hold and resume the normal director once stable. Source
  camera cuts keep their timestamps.
- Crowds: the number of panels is four; the detector is no longer truncated to
  four people. Five or more stable, verified faces are partitioned into four
  spatial groups. Each group panel fits its members' padded head envelopes.
  Every planned face belongs to exactly one panel. Spatial ordering stays fixed
  during the shot rather than shuffling with each word.
  Unfilled space in crowd panels uses a quiet black matte, not blurred copies
  of other participants. Source pixels and head coverage remain authoritative.
- Existing four-person grids, single portraits, stacked and diagonal splits,
  144 caption presets, music/B-roll removal and three source methods remain.

Crowd framing depends on faces being visible and detectable in the source.
It cannot create missing/off-camera faces, restore missing source detail or
certify speech recognition on every language or every podcast. Sampled QA is
not an every-frame accuracy guarantee. Low-confidence occurrences retain a
shared/full-source view. No additional music, SFX or B-roll is added.

Rotation reference: [FFmpeg documentation](https://ffmpeg.org/ffmpeg.html).
