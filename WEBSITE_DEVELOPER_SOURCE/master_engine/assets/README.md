# Master Editor V7

Independent plan-first editing. Legacy AI Clipper and AI Video Editor function
bodies are preserved. Two reversible hook statements connect the new menu.

## Current behaviour

- The visible source menu keeps exactly Full File, File + Timestamp and YouTube
  + Timestamp. Caption and frame selection is automatic. The delivered result
  includes Remove Captions and timestamped Transcript controls.
- The XML option uses the included new smooth-follow preset, not the legacy XML
  renderer or arbitrary third-party XML effects.
- No added music, SFX, stock or placeholder/AI B-roll. The old loading and mixing
  paths are removed. Original source audio remains; existing music already mixed
  into that recording is NOT separated from speech.
- One verified person gets a centred medium or close shot. An already-composed
  vertical portrait is preserved instead of being zoomed a second time. A rare
  detail/body-context view requires a sustained solo shot and an evidence cue.
  Verified multi-person moments up to exactly 2 seconds use the one controlled
  blur-backed fit. Longer two-person dialogue can use
  equal 50/50 stacked, upper-left/lower-right diagonal or central duo-band views. Three/four
  verified participants use three/four-panel views. Auto Split OFF retains one
  shared image instead.
- A sustained single shot is allowed to remain one layout. The engine does not
  cycle through 20 available layouts merely to advertise variety. Available
  source pixels must substantially fill a split; already-tight inputs use the
  central duo band instead of floating sharp cards over a large blurred canvas.
- A long two-person answer can change split composition at an important new
  sentence/idea, at least eight seconds apart; no timer-only frame cycling.
- Face-anchored, fixed-scale paths follow subject movement smoothly. Offline
  smoothing avoids a causal camera-follow delay. Padded head envelopes, limited
  pan speed and brief-detection-dropout tolerance reduce cropped heads/jitter.
- Small closed-mouth/breath gaps bounded by the same speaker are joined. Real
  short responses and overlapping/uncertain speech retain a shared view.
- Source cuts are scanned at source frame rate before the 0.2-second face pass.
  Initial mouth-analysis warm-up is backfilled only by a confirmed following
  turn. A verified turn is not delayed simply to finish a layout timer.
- 192 caption looks across 32 typography recipes (six colour/size variations per
  recipe), not 192 independent animation algorithms. The recipes include bold
  word highlights, white/lime stacks, yellow italic emphasis, coral serif words,
  mixed-case editorial, quiet white lines, karaoke, word plates and ribbons.
  Montserrat, Anton, Barlow Condensed and DM Serif Display are bundled with OFL
  licences; DejaVu Sans Mono uses the system font. Measurement uses those assets.
- Measured colour, brightness, clutter, source detail and speech pace determine
  eligible styles. A creator-scoped SQLite history avoids the immediately prior
  family when an equally suitable alternative exists. Recent styles are penalized.
  The same video retry keeps its chosen look. History failure degrades to stable
  content-based selection and is reported; it does not fail the video job.
- Captions retain at most two measured lines, lower-centre in solo shots, at
  split seams and below the central duo band. Phrase geometry stays stable;
  small speech gaps are bridged and a word crossing a cut changes position at
  that cut. Motion peaks are limited to 103%, without random spins or bounces.
- A transcript-grounded hook/title and estimated local editorial score accompany
  the result. This is not a real TikTok measurement or a prediction of virality.
  A compact white title card is rendered only if its reserved lane is face-clear,
  including native vertical footage. It never substitutes invented speech.
- Restrained noise reduction, dialogue compression/loudness, colour and sharpness
  polish. Low-resolution/soft inputs alone receive mild hqdn3d restoration;
  clean sources bypass it. Cleanup cannot recreate missing detail or speech.
- JSON plans are saved and validated before rendering. Output checks cover frame
  geometry, timestamps, codec, audio, black frames and sampled face visibility.
  A failed occurrence can be repaired locally; other planned layouts are retained.

## Included resources

The inspectable catalogue is in caption_templates.json. OpenCV YuNet 2023 and
2026 models are bundled with their upstream MIT licence. research_notes.json is
a dated record of representative official guidance, not a live scraper, copied
proprietary templates, or an exhaustive survey of all social media.

## Install/runtime

Upload install_master_editor_engine_v7.sh into /root/editingbot and run it with
bash. It checks the current environment without upgrading the old bot's
dependencies. Backups retain the previous bot, Master engine and owned assets.
Failed installs restore those backups. The journal timestamp uses a compatible
space-separated format, checked before bot changes.

Python needs numpy, Pillow, OpenCV 4.8+ or 5, faster-whisper, yt-dlp and
python-telegram-bot. FFmpeg needs libx264, AAC, libass and the checked video/audio
filters including select/showinfo. Eight bundled font files provide caption
typography; fontconfig and DejaVu/Liberation remain the system fallback.

Whisper weights may download on the first audio job. The English default is
distil-large-v3; use MASTER_WHISPER_MODEL=large-v3 for multilingual speech if
resources allow. Captions transcribe, not translate. Recognition and every-frame
tracking cannot be guaranteed on unseen videos.

Output is 1080x1920 H.264 High/BT.709, default CRF15, source-aware FPS (including
fractional FPS), AAC256k and fast-start MP4. A poor input cannot become genuine
high-detail footage by exporting at 1080p. No TikTok eligibility/reach promise.

Rendered files survive a Telegram delivery failure. Plans/reports are retained
under master_engine_jobs/reports; MASTER_SEND_REPORTS=1 also sends them in chat.
Runtime depends on footage length, CPU/GPU, memory, storage and download access.

## Verification boundary

Local tests use controlled source clips, real FFmpeg/libass, real YuNet face
detection, reference-derived stills and synthetic subject motion. Those test
transcripts are fixtures, not measured live-speech recognition. The user's
original and faulty edited MP4 files were not available for this revision.
Screenshots establish design intent but cannot establish speaker timing, A/V
sync, compression quality or tracking accuracy in those unavailable videos.
The installer is tested against strict system-service stubs, not the user's VPS.
