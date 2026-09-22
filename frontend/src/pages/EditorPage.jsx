import React, { useState } from 'react';
import { Upload, Sparkles, Download, Loader2, AlertCircle, Info, Play } from 'lucide-react';
import YoutubeIcon from '../components/common/YoutubeIcon';
import TimelineTrimmer from '../components/editor/TimelineTrimmer';
import { api, resolveApiUrl } from '../api';
import { useJobPolling } from '../hooks/useJobPolling';

const MAX_UPLOAD_MB = 500;

export default function EditorPage({ user, onRequireAuth }) {
  const [sourceType, setSourceType] = useState('youtube'); // 'youtube' or 'file'
  const [youtubeUrl, setYoutubeUrl] = useState('');
  const [metadata, setMetadata] = useState(null);
  const [file, setFile] = useState(null);

  // Timeline selection states
  const [startTime, setStartTime] = useState(0);
  const [endTime, setEndTime] = useState(60);
  const [duration, setDuration] = useState(600);

  // Styling options
  const [captionStyle, setCaptionStyle] = useState('hormozi');
  const [layoutMode, setLayoutMode] = useState('focus');

  // Job & processing states
  const [fetchingMeta, setFetchingMeta] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [jobError, setJobError] = useState('');
  const [isSimulation, setIsSimulation] = useState(false);

  const handleFinished = (res) => {
    if (res.status === 'completed' && res.result_data) {
      setResult(res.result_data);
      setIsSimulation(Boolean(res.result_data.is_simulation));
      setLoading(false);
    } else {
      setJobError(res.error_message || 'Video editor rendering failed');
      setLoading(false);
    }
  };

  const { activeJob, startPolling } = useJobPolling(handleFinished);

  const handleFetchMetadata = async (url) => {
    if (!url || (!url.includes('youtube.com') && !url.includes('youtu.be'))) return;
    setError('');
    setFetchingMeta(true);
    try {
      const meta = await api.youtube.getMetadata(url);
      setMetadata(meta);
      const totalSecs = Math.max(10, Math.floor(meta.duration || 600));
      setDuration(totalSecs);
      setStartTime(0);
      setEndTime(Math.min(60, totalSecs));
    } catch (err) {
      setError(err.message || 'Could not fetch YouTube video details.');
    } finally {
      setFetchingMeta(false);
    }
  };

  const handleProcessEdit = async () => {
    if (!user) {
      onRequireAuth();
      return;
    }

    setError('');
    setJobError('');
    setLoading(true);
    setResult(null);

    try {
      let sourceUrl = youtubeUrl;
      let title = metadata?.title || 'Custom Video Edit';

      if (sourceType === 'file') {
        if (!file) throw new Error('Please select a video file to upload.');
        if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
          throw new Error(`File exceeds the ${MAX_UPLOAD_MB}MB upload limit.`);
        }
        const uploadRes = await api.files.upload(file);
        sourceUrl = uploadRes.url_base || uploadRes.url;
        title = file.name;
      } else {
        if (!youtubeUrl) throw new Error('Please enter a valid YouTube URL.');
      }

      const res = await api.editor.process({
        source_type: sourceType,
        source_url: sourceUrl,
        title: title,
        thumbnail_url: metadata?.thumbnail,
        start_seconds: startTime,
        end_seconds: endTime,
        caption_style: captionStyle,
        layout_mode: layoutMode,
      });

      startPolling({ id: res.job_id, status: 'queued', progress: 0, stage: 'Queued' });
    } catch (err) {
      setError(err.message || 'Failed to start AI Video Editor.');
      setLoading(false);
    }
  };

  const isProcessing = activeJob && activeJob.status !== 'completed' && activeJob.status !== 'failed';

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="text-center max-w-xl mx-auto">
        <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-[6px] bg-[#edf5f3] border border-[#c4e3dc] text-[#0f766e] text-[10px] font-mono font-bold uppercase tracking-[0.08em] mb-2">
          RANGE-FIRST PIPELINE
        </div>
        <h1 className="text-2xl md:text-3xl font-bold text-[#0f172a]">
          Timeline Video Editor
        </h1>
        <p className="text-xs md:text-sm text-[#526173] mt-1">
          Select exact start and end seconds. Range-first transport downloads only your selected slice, re-centering active speakers to 9:16.
        </p>
      </div>

      {/* Input Source & Timeline Card */}
      <div className="theme-card p-6 md:p-8 space-y-5">
        {/* Source Switcher */}
        <div className="flex gap-2 max-w-sm mx-auto bg-[#eaf0f2] p-1 rounded-[9px] border border-[#d4dee4]" role="group" aria-label="Source type">
          <button
            type="button"
            onClick={() => setSourceType('youtube')}
            aria-pressed={sourceType === 'youtube'}
            className={`flex-1 py-1.5 rounded-[7px] text-xs font-semibold flex items-center justify-center gap-1.5 transition-all ${
              sourceType === 'youtube' ? 'bg-white text-[#0f766e] shadow-sm' : 'text-[#526173] hover:text-[#0f172a]'
            }`}
          >
            <YoutubeIcon className="w-3.5 h-3.5 text-red-600" />
            <span>YouTube URL</span>
          </button>
          <button
            type="button"
            onClick={() => setSourceType('file')}
            aria-pressed={sourceType === 'file'}
            className={`flex-1 py-1.5 rounded-[7px] text-xs font-semibold flex items-center justify-center gap-1.5 transition-all ${
              sourceType === 'file' ? 'bg-white text-[#0f766e] shadow-sm' : 'text-[#526173] hover:text-[#0f172a]'
            }`}
          >
            <Upload className="w-3.5 h-3.5 text-[#0f766e]" />
            <span>Upload File</span>
          </button>
        </div>

        {sourceType === 'youtube' ? (
          <div>
            <label htmlFor="editor-url" className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
              YouTube Video Link
            </label>
            <div className="flex gap-2">
              <div className="relative flex-1">
                <div className="absolute left-3.5 top-1/2 -translate-y-1/2">
                  <YoutubeIcon className="w-4 h-4 text-red-600" />
                </div>
                <input
                  id="editor-url"
                  type="url"
                  value={youtubeUrl}
                  onChange={(e) => setYoutubeUrl(e.target.value)}
                  onBlur={() => handleFetchMetadata(youtubeUrl)}
                  placeholder="https://www.youtube.com/watch?v=... or https://youtu.be/..."
                  className="input-field has-leading-icon"
                />
              </div>
              <button
                type="button"
                onClick={() => handleFetchMetadata(youtubeUrl)}
                disabled={fetchingMeta || !youtubeUrl}
                className="btn-secondary text-xs py-2 px-4 shrink-0"
              >
                {fetchingMeta ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : 'Load Metadata'}
              </button>
            </div>
            <p className="text-[11px] text-[#64748b] mt-1 font-mono">
              Metadata fetched in &lt;1.5s via range-first query without full video download.
            </p>
          </div>
        ) : (
          <div>
            <label className="theme-well p-6 flex flex-col items-center justify-center cursor-pointer border border-dashed border-[#b5c7d3] hover:border-[#0f766e] transition-colors rounded-[9px]">
              <Upload className="w-7 h-7 text-[#0f766e] mb-1.5" />
              <span className="text-xs font-semibold text-[#0f172a]">
                {file ? file.name : 'Select source video file'}
              </span>
              <span className="text-[11px] text-[#64748b] mt-0.5 font-mono">
                {file
                  ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
                  : `MP4, MOV, MKV supported (max ${MAX_UPLOAD_MB} MB)`}
              </span>
              <input
                type="file"
                accept="video/*"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setFile(e.target.files[0]);
                    setDuration(300);
                    setStartTime(0);
                    setEndTime(60);
                  }
                }}
                className="hidden"
              />
            </label>
          </div>
        )}

        {/* Video Thumbnail & Metadata Preview */}
        {metadata && sourceType === 'youtube' && (
          <div className="theme-well p-3 flex flex-col sm:flex-row items-center gap-3">
            <img
              src={metadata.thumbnail}
              alt={metadata.title || 'Video thumbnail'}
              className="w-full sm:w-40 h-24 object-cover rounded-[6px] shrink-0 border border-[#d4dee4]"
            />
            <div className="text-left flex-1 min-w-0">
              <span className="badge-teal text-[10px] mb-1">
                {metadata.channel || 'YouTube'}
              </span>
              <h4 className="font-semibold text-xs text-[#0f172a] line-clamp-2">
                {metadata.title}
              </h4>
              <div className="font-mono text-[11px] text-[#64748b] mt-1 tabular-nums">
                Duration: <span className="font-bold text-[#0f172a]">{metadata.duration_formatted}</span>
              </div>
            </div>
          </div>
        )}

        {/* Horizontal Interactive Timeline Trimmer */}
        {(metadata || file) && (
          <TimelineTrimmer
            duration={duration}
            startTime={startTime}
            endTime={endTime}
            onChange={(s, e) => {
              setStartTime(s);
              setEndTime(e);
            }}
          />
        )}

        {/* Style & Layout Presets */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-3 border-t border-[#e2e8f0]">
          {/* Caption Style */}
          <div>
            <div className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
              Caption Typography Preset
            </div>
            <div className="grid grid-cols-3 gap-1.5" role="group" aria-label="Caption style">
              {[
                { id: 'hormozi', label: '🔥 Hormozi' },
                { id: 'mrbeast', label: '⚡ MrBeast' },
                { id: 'neon', label: '✨ Neon' },
                { id: 'clean', label: '◻️ Clean' },
                { id: 'none', label: '🚫 None' },
              ].map((style) => (
                <button
                  key={style.id}
                  type="button"
                  onClick={() => setCaptionStyle(style.id)}
                  aria-pressed={captionStyle === style.id}
                  className={`text-xs py-1.5 px-2 rounded-[7px] font-semibold border transition-all ${
                    captionStyle === style.id
                      ? 'bg-[#edf5f3] text-[#0f766e] border-[#c4e3dc]'
                      : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                  }`}
                >
                  {style.label}
                </button>
              ))}
            </div>
          </div>

          {/* Reframing Layout */}
          <div>
            <div className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
              Framing & Geometry Layout
            </div>
            <div className="grid grid-cols-2 gap-1.5" role="group" aria-label="Layout mode">
              {[
                { id: 'focus', label: '🎯 Focus (1 Speaker)' },
                { id: 'duo_context', label: '👥 Duo Split Screen' },
                { id: 'fit_blur', label: '💧 Fit with Blur' },
                { id: 'passthrough', label: '↔️ Original Aspect' },
              ].map((layout) => (
                <button
                  key={layout.id}
                  type="button"
                  onClick={() => setLayoutMode(layout.id)}
                  aria-pressed={layoutMode === layout.id}
                  className={`text-xs py-1.5 px-2 rounded-[7px] font-semibold border transition-all ${
                    layoutMode === layout.id
                      ? 'bg-[#edf5f3] text-[#0f766e] border-[#c4e3dc]'
                      : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                  }`}
                >
                  {layout.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {(error || jobError) && (
          <div className="p-3 bg-[#fef2f2] border border-[#fecaca] text-[#b91c1c] text-xs rounded-[9px] flex items-center gap-2" role="alert">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error || jobError}</span>
          </div>
        )}

        {/* Process Button */}
        <button
          type="button"
          onClick={handleProcessEdit}
          disabled={loading || (!metadata && !file)}
          className="btn-primary w-full text-xs font-semibold py-3 shadow-md"
        >
          {loading ? (
            <>
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Processing Range with MasterEngine...</span>
            </>
          ) : (
            <>
              <Sparkles className="w-4 h-4" />
              <span>Process Selected Range ({Math.round(endTime - startTime)}s)</span>
            </>
          )}
        </button>
      </div>

      {/* Active Job Progress */}
      {isProcessing && (
        <div className="theme-card p-5 border-l-4 border-l-[#0f766e]" role="status" aria-live="polite">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-[#0f172a]">
              <Loader2 className="w-4 h-4 text-[#0f766e] animate-spin" />
              <span>{activeJob.stage || 'Executing range-first extraction...'}</span>
            </div>
            <span className="font-mono text-xs font-bold text-[#0f766e] tabular-nums">
              {Math.round(activeJob.progress || 0)}%
            </span>
          </div>
          <div
            className="w-full h-2 bg-[#eaf0f2] rounded-full overflow-hidden"
            role="progressbar"
            aria-valuenow={Math.round(activeJob.progress || 0)}
            aria-valuemin={0}
            aria-valuemax={100}
          >
            <div
              className="h-full bg-[#0f766e] rounded-full transition-all duration-300"
              style={{ width: `${activeJob.progress || 10}%` }}
            />
          </div>
        </div>
      )}

      {/* Completed Result Card */}
      {result && (
        <div className="theme-card p-6 text-left">
          <div className="flex items-center gap-2 text-[#0f766e] font-semibold text-xs mb-4">
            <Sparkles className="w-4 h-4" />
            <span>Range Execution Complete</span>
          </div>

          {isSimulation && (
            <div className="mb-4 p-3 rounded-[9px] bg-[#fffbeb] border border-[#fde68a] text-[#b45309] text-xs flex items-start gap-2" role="status">
              <Info className="w-4 h-4 shrink-0 mt-0.5" />
              <span>
                Simulation mode: no video file was rendered. The server needs
                ENABLE_HEAVY_RENDERING=1 and a source file it can process to produce a download.
              </span>
            </div>
          )}

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 items-center">
            <div className="theme-well p-4 rounded-[9px] flex items-center justify-center aspect-[9/16] max-w-[220px] mx-auto bg-[#0f172a] relative">
              <div className="text-center text-white p-3">
                <Play className="w-10 h-10 text-[#0f766e] mx-auto mb-2 opacity-90" />
                <span className="text-xs font-mono font-bold block">9:16 Render</span>
                <span className="text-[10px] text-gray-400 block mt-0.5 font-mono tabular-nums">
                  Duration: {Math.round(result.duration)}s
                </span>
              </div>
            </div>

            <div className="space-y-3">
              <h3 className="text-base font-bold text-[#0f172a]">
                {result.title || 'AI Edited Clip'}
              </h3>

              <div className="space-y-1.5 text-xs text-[#526173]">
                <div className="flex justify-between py-1 border-b border-[#e2e8f0]">
                  <span className="font-semibold">Selected Window:</span>
                  <span className="font-mono font-bold text-[#0f766e] tabular-nums">
                    {result.start_seconds}s - {result.end_seconds}s
                  </span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#e2e8f0]">
                  <span className="font-semibold">Subtitles:</span>
                  <span className="capitalize">{result.caption_style}</span>
                </div>
                <div className="flex justify-between py-1 border-b border-[#e2e8f0]">
                  <span className="font-semibold">Layout:</span>
                  <span className="capitalize">{result.layout}</span>
                </div>
              </div>

              <div className="pt-3">
                {result.output_video ? (
                  <a
                    href={resolveApiUrl(result.output_video)}
                    download="edited_clip.mp4"
                    className="btn-primary w-full text-xs py-2.5 font-semibold flex items-center justify-center gap-2"
                  >
                    <Download className="w-3.5 h-3.5" />
                    <span>Download Edited MP4</span>
                  </a>
                ) : (
                  <button
                    type="button"
                    disabled
                    className="w-full text-xs py-2.5 font-semibold rounded-[9px] border border-[#d4dee4] bg-[#eaf0f2] text-[#64748b] flex items-center justify-center gap-2 cursor-not-allowed"
                  >
                    <Info className="w-3.5 h-3.5" />
                    <span>No file produced (simulation mode)</span>
                  </button>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
