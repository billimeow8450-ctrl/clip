import React, { useState, useEffect } from 'react';
import { Scissors, Sparkles, Upload, Play, Download, CheckCircle2, Loader2, AlertCircle, Flame, Clock } from 'lucide-react';
import YoutubeIcon from '../components/common/YoutubeIcon';
import { api } from '../api';

export default function ClipperPage({ user, onRequireAuth }) {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState(null);
  const [analysisMode, setAnalysisMode] = useState('deep'); // 'quick' or 'deep'
  const [targetDuration, setTargetDuration] = useState('60'); // '30', '60', '90', '120', 'all'
  const [loading, setLoading] = useState(false);
  const [activeJob, setActiveJob] = useState(null);
  const [clips, setClips] = useState([]);
  const [error, setError] = useState('');

  // Poll active job status
  useEffect(() => {
    let timer = null;
    if (activeJob && activeJob.status !== 'completed' && activeJob.status !== 'failed') {
      timer = setInterval(async () => {
        try {
          const res = await api.jobs.get(activeJob.id);
          setActiveJob(res);
          if (res.status === 'completed' && res.result_data?.clips) {
            setClips(res.result_data.clips);
            setLoading(false);
          } else if (res.status === 'failed') {
            setError(res.error_message || 'Clipper processing failed');
            setLoading(false);
          }
        } catch (err) {
          console.error(err);
        }
      }, 1500);
    }
    return () => clearInterval(timer);
  }, [activeJob]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!user) {
      onRequireAuth();
      return;
    }

    setError('');
    let inputUrl = url.trim();

    if (!inputUrl && !file) {
      setError('Please provide a YouTube URL or upload a video file.');
      return;
    }

    setLoading(true);
    setClips([]);

    try {
      if (file && !inputUrl) {
        const uploadRes = await api.files.upload(file);
        inputUrl = uploadRes.url;
      }

      const res = await api.clipper.process({
        url: inputUrl,
        title: file ? file.name : 'YouTube Viral Clip Analysis',
        analysis_mode: analysisMode,
        target_duration: targetDuration,
      });

      setActiveJob({ id: res.job_id, status: 'queued', progress: 0, stage: 'Queued' });
    } catch (err) {
      setError(err.message || 'Failed to submit clipping job.');
      setLoading(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="text-center max-w-xl mx-auto">
        <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-[6px] bg-[#edf5f3] border border-[#c4e3dc] text-[#0f766e] text-[10px] font-mono font-bold uppercase tracking-[0.08em] mb-2">
          VIRAL INTELLIGENCE MODULE
        </div>
        <h1 className="text-2xl md:text-3xl font-bold text-[#0f172a]">
          Auto Clipper Pro
        </h1>
        <p className="text-xs md:text-sm text-[#526173] mt-1">
          Analyzes audience retention, comments, and dialogue hooks to isolate and reframe viral 9:16 vertical video segments.
        </p>
      </div>

      {/* Main Form Card */}
      <div className="theme-card p-6 md:p-8">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* YouTube URL input */}
          <div>
            <label className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
              Source YouTube URL
            </label>
            <div className="relative">
              <div className="absolute left-3.5 top-1/2 -translate-y-1/2">
                <YoutubeIcon className="w-4 h-4 text-red-600" />
              </div>
              <input
                type="url"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setFile(null); }}
                placeholder="https://www.youtube.com/watch?v=... or https://youtu.be/..."
                className="input-field pl-10"
              />
            </div>
          </div>

          <div className="flex items-center gap-3 my-1">
            <div className="flex-1 border-t border-[#e2e8f0]" />
            <span className="text-[10px] font-mono font-semibold text-[#64748b] uppercase tracking-wider">OR LOCAL UPLOAD</span>
            <div className="flex-1 border-t border-[#e2e8f0]" />
          </div>

          {/* File Upload Dropzone */}
          <div>
            <label className="theme-well p-5 flex flex-col items-center justify-center cursor-pointer border border-dashed border-[#b5c7d3] hover:border-[#0f766e] transition-colors rounded-[9px]">
              <Upload className="w-6 h-6 text-[#0f766e] mb-1.5" />
              <span className="text-xs font-semibold text-[#0f172a]">
                {file ? file.name : 'Select MP4, MOV, or MKV video file'}
              </span>
              <span className="text-[11px] text-[#64748b] mt-0.5 font-mono">
                {file ? `${(file.size / (1024 * 1024)).toFixed(1)} MB` : 'Max 2 GB source video file'}
              </span>
              <input
                type="file"
                accept="video/*"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setFile(e.target.files[0]);
                    setUrl('');
                  }
                }}
                className="hidden"
              />
            </label>
          </div>

          {/* Settings Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-1">
            {/* Analysis Mode */}
            <div>
              <label className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
                Detection Engine Mode
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setAnalysisMode('quick')}
                  className={`text-xs py-2 px-3 rounded-[9px] font-semibold border transition-all ${
                    analysisMode === 'quick'
                      ? 'bg-[#edf5f3] text-[#0f766e] border-[#c4e3dc]'
                      : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                  }`}
                >
                  ⚡ Quick Heuristic
                </button>
                <button
                  type="button"
                  onClick={() => setAnalysisMode('deep')}
                  className={`text-xs py-2 px-3 rounded-[9px] font-semibold border transition-all ${
                    analysisMode === 'deep'
                      ? 'bg-[#edf5f3] text-[#0f766e] border-[#c4e3dc]'
                      : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                  }`}
                >
                  🔥 Deep Comment AI
                </button>
              </div>
            </div>

            {/* Target Duration */}
            <div>
              <label className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
                Target Clip Duration
              </label>
              <div className="grid grid-cols-5 gap-1.5">
                {['30', '60', '90', '120', 'all'].map((dur) => (
                  <button
                    key={dur}
                    type="button"
                    onClick={() => setTargetDuration(dur)}
                    className={`text-xs py-2 font-mono font-semibold rounded-[9px] border transition-all ${
                      targetDuration === dur
                        ? 'bg-[#0f172a] text-white border-[#0f172a]'
                        : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                    }`}
                  >
                    {dur === 'all' ? 'All' : `${dur}s`}
                  </button>
                ))}
              </div>
            </div>
          </div>

          {error && (
            <div className="p-3 bg-[#fef2f2] border border-[#fecaca] text-[#b91c1c] text-xs rounded-[9px] flex items-center gap-2">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {/* Submit CTA */}
          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full text-xs font-semibold py-3 shadow-md"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Executing Viral Analysis & MasterEngine Reframing...</span>
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4" />
                <span>Analyze & Generate Viral Clips</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Active Job Progress */}
      {activeJob && activeJob.status !== 'completed' && (
        <div className="theme-card p-5 border-l-4 border-l-[#0f766e]">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-[#0f172a]">
              <Loader2 className="w-4 h-4 text-[#0f766e] animate-spin" />
              <span>{activeJob.stage || 'Analyzing media streams...'}</span>
            </div>
            <span className="font-mono text-xs font-bold text-[#0f766e] tabular-nums">
              {Math.round(activeJob.progress || 0)}%
            </span>
          </div>

          <div className="w-full h-2 bg-[#eaf0f2] rounded-full overflow-hidden">
            <div
              className="h-full bg-[#0f766e] rounded-full transition-all duration-300"
              style={{ width: `${activeJob.progress || 10}%` }}
            />
          </div>
          <div className="text-[11px] text-[#64748b] mt-2 font-mono">
            Pipeline: Whisper Speech Alignment → YuNet Face Tracking → 9:16 Encode
          </div>
        </div>
      )}

      {/* Generated Clips Grid */}
      {clips.length > 0 && (
        <div className="space-y-4">
          <div className="flex items-center justify-between border-b border-[#d4dee4] pb-2">
            <h2 className="text-lg font-bold text-[#0f172a]">
              Generated Viral Candidates ({clips.length})
            </h2>
            <span className="text-xs font-mono text-[#526173]">
              Format: 9:16 Vertical MP4
            </span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
            {clips.map((clip, idx) => (
              <div key={clip.id || idx} className="theme-card p-3.5 flex flex-col justify-between">
                {/* 9:16 Thumbnail */}
                <div className="relative aspect-[9/16] rounded-[9px] overflow-hidden bg-[#0f172a] mb-3 group">
                  <img
                    src={clip.thumbnail_url}
                    alt={clip.title}
                    className="w-full h-full object-cover group-hover:scale-103 transition-transform duration-200"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-black/20" />

                  {/* Viral Score Badge */}
                  <div className="absolute top-2.5 left-2.5 px-2 py-0.5 rounded-[4px] bg-white/95 text-[#966915] font-mono text-[10px] font-bold flex items-center gap-1 shadow-sm border border-[#ebd8ad]">
                    <Flame className="w-3 h-3 text-[#966915]" />
                    <span>{clip.viral_score} SCORE</span>
                  </div>

                  {/* Duration Badge */}
                  <div className="absolute top-2.5 right-2.5 px-1.5 py-0.5 rounded-[4px] bg-black/60 text-white font-mono text-[10px] font-medium tabular-nums">
                    {Math.round(clip.duration)}s
                  </div>

                  {/* Hook Text */}
                  <div className="absolute bottom-3 left-2.5 right-2.5 text-white text-left">
                    <div className="text-[9px] font-mono font-bold text-[#fde68a] uppercase tracking-wider">HOOK TRIGGER</div>
                    <p className="text-xs font-medium leading-snug line-clamp-2 mt-0.5">
                      "{clip.hook_text || clip.title}"
                    </p>
                  </div>
                </div>

                <div className="text-left mb-3">
                  <h4 className="font-semibold text-xs text-[#0f172a] line-clamp-1">
                    {clip.title}
                  </h4>
                  <span className="text-[11px] text-[#64748b] font-mono tabular-nums">
                    T: {Math.round(clip.start_time)}s - {Math.round(clip.end_time)}s
                  </span>
                </div>

                <a
                  href={clip.video_url}
                  download={`clip_${idx + 1}.mp4`}
                  className="btn-primary w-full text-xs py-2 font-semibold flex items-center justify-center gap-1.5"
                >
                  <Download className="w-3.5 h-3.5" />
                  <span>Download MP4</span>
                </a>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
