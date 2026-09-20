# Master Editor 6.2.0 — FFmpeg runtime correction

The reported `-11` is a native SIGSEGV. A swscaler data-alignment warning by
itself is not a diagnosis of its cause. The original failing source and VPS
FFmpeg build were not available for reproduction.

## Changes

- One planned composition per FFmpeg graph, with the same source/output frame
  boundaries. Many separate crop/blur/overlay timelines are no longer resident
  in a single graph.
- Exact frame-quantised concat durations prevent the container's duration
  estimate from accumulating drift over many short fractional-FPS chunks.
- One filter thread and bounded decoder/encoder threads, instead of automatic
  codec thread allocation on large/shared VPS CPU topologies.
- Explicit 8-bit YUV420 source/overlay negotiation and even background scaling.
  The latter uses established scale expressions, not a newer scale option
  missing from older FFmpeg builds.
- One compatibility retry after SIGSEGV, SIGBUS, SIGABRT or SIGILL. That retry
  disables FFmpeg SIMD and x264 assembly and uses one codec thread. It does not
  replace the planned layouts, remove captions, lower resolution or reduce CRF.
  Later shots and finishing retain this mode after a successful recovery.
- Ordinary invalid input, cancellation, timeout and SIGKILL are not retried as
  native CPU/filter crashes. A second native crash stops, rather than looping
  or returning an incomplete output.
- The entire final video/audio stream is decoded with `-xerror` before output
  QA and delivery. A successful encode exit status alone is not treated as
  proof of packet integrity.
- Private diagnostic JSON files survive job cleanup under
  `master_engine_cache/render_diagnostics`. They include native status,
  command, filter graph, FFmpeg build, stream formats and bounded stderr.
  They do not contain source media, transcript text or environment variables.
- Installation renders and decodes a short 1080x1920 test with solo, stacked,
  diagonal, six-person/four-panel compositions, ASS captions and source audio
  before modifying bot files or restarting the service. It imports the staged
  payload even when invoked from the existing bot directory.

The original menu/hook/caption catalogue, music/B-roll restrictions and
subject/planner algorithms are preserved. Legacy editor bodies are not edited.

## Limits and troubleshooting

The compatibility path can render more slowly. It cannot repair every damaged
file, unsupported FFmpeg build, operating-system fault or exhausted machine.
Local checks and injected crash recovery are not a live VPS or speech-model test.

The installer runs the renderer probe automatically. It can also be run as:

```sh
python3 -m master_engine --render-self-test
MASTER_FFMPEG_SAFE_MODE=1 python3 -m master_engine --render-self-test
```

Use the bot's virtual-environment Python instead of `python3` when applicable.
If a job still fails, share its diagnostic JSON plus a short failing source
sample. No full source media is uploaded by the diagnostic mechanism.

## Technical references

- [Python subprocess return codes](https://docs.python.org/3/library/subprocess.html#subprocess.CompletedProcess.returncode)
- [FFmpeg CPU and thread options](https://ffmpeg.org/ffmpeg.html)
- [FFmpeg overlay format negotiation](https://ffmpeg.org/ffmpeg-filters.html#overlay)
