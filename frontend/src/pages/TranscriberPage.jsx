import React, { useState } from 'react';
import { Upload, Download, Search, Copy, Check, Sparkles, Loader2, AlertCircle, Info } from 'lucide-react';
import YoutubeIcon from '../components/common/YoutubeIcon';
import { api, resolveApiUrl } from '../api';
import { useJobPolling } from '../hooks/useJobPolling';

const MAX_UPLOAD_MB = 500;

export default function TranscriberPage({ user, onRequireAuth }) {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState(null);
  const [language, setLanguage] = useState('en');
  const [exportFormat, setExportFormat] = useState('txt');
  const [searchQuery, setSearchQuery] = useState('');
  const [copied, setCopied] = useState(false);

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
      setJobError(res.error_message || 'Transcription failed');
      setLoading(false);
    }
  };

  const { activeJob, startPolling } = useJobPolling(handleFinished);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!user) {
      onRequireAuth();
      return;
    }

    setError('');
    setJobError('');
    let inputSource = url.trim();

    if (!inputSource && !file) {
      setError('Please provide a YouTube URL or audio/video file.');
      return;
    }

    if (file && file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`File exceeds the ${MAX_UPLOAD_MB}MB upload limit.`);
      return;
    }

    setLoading(true);
    setResult(null);

    try {
      if (file && !inputSource) {
        const uploadRes = await api.files.upload(file);
        inputSource = uploadRes.url_base || uploadRes.url;
      }

      const res = await api.transcript.process({
        url_or_file: inputSource,
        title: file ? file.name : 'Whisper Transcript',
        language,
        export_format: exportFormat,
      });

      startPolling({ id: res.job_id, status: 'queued', progress: 0, stage: 'Queued' });
    } catch (err) {
      setError(err.message || 'Failed to submit transcription job.');
      setLoading(false);
    }
  };

  const handleCopy = async () => {
    if (!result?.full_text) return;
    try {
      await navigator.clipboard.writeText(result.full_text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API can fail on non-secure origins; text stays visible regardless.
    }
  };

  const filteredSegments = (result?.segments || []).filter((seg) => {
    if (!seg) return false;
    const haystack = `${seg.text || ''} ${seg.speaker || ''}`.toLowerCase();
    return haystack.includes(searchQuery.toLowerCase());
  });

  const exportHref = result?.export_files?.[exportFormat];

  return (
    <div className="max-w-4xl mx-auto space-y-8 pb-16">
      {/* Header */}
      <div className="text-center max-w-xl mx-auto">
        <div className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-[6px] bg-[#edf5f3] border border-[#c4e3dc] text-[#0f766e] text-[10px] font-mono font-bold uppercase tracking-[0.08em] mb-2">
          ACOUSTIC SPEECH-TO-TEXT
        </div>
        <h1 className="text-2xl md:text-3xl font-bold text-[#0f172a]">
          Whisper Transcriber
        </h1>
        <p className="text-xs md:text-sm text-[#526173] mt-1">
          Generate timestamped, word-level transcripts from audio/video streams with local acoustic models.
        </p>
      </div>

      {/* Input Card */}
      <div className="theme-card p-6 md:p-8">
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="transcriber-url" className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
              Source YouTube Link
            </label>
            <div className="relative">
              <div className="absolute left-3.5 top-1/2 -translate-y-1/2">
                <YoutubeIcon className="w-4 h-4 text-red-600" />
              </div>
              <input
                id="transcriber-url"
                type="url"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setFile(null); }}
                placeholder="https://www.youtube.com/watch?v=..."
                className="input-field has-leading-icon"
              />
            </div>
          </div>

          <div className="flex items-center gap-3 my-1">
            <div className="flex-1 border-t border-[#e2e8f0]" />
            <span className="text-[10px] font-mono font-semibold text-[#64748b] uppercase tracking-wider">OR MEDIA UPLOAD</span>
            <div className="flex-1 border-t border-[#e2e8f0]" />
          </div>

          <div>
            <label className="theme-well p-5 flex flex-col items-center justify-center cursor-pointer border border-dashed border-[#b5c7d3] hover:border-[#0f766e] transition-colors rounded-[9px]">
              <Upload className="w-6 h-6 text-[#0f766e] mb-1.5" />
              <span className="text-xs font-semibold text-[#0f172a]">
                {file ? file.name : 'Select audio or video file'}
              </span>
              <span className="text-[11px] text-[#64748b] mt-0.5 font-mono">
                {file
                  ? `${(file.size / (1024 * 1024)).toFixed(1)} MB`
                  : `MP4, MP3, WAV, MOV, M4A (max ${MAX_UPLOAD_MB} MB)`}
              </span>
              <input
                type="file"
                accept="audio/*,video/*"
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

          {/* Options */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5 pt-1">
            {/* Language */}
            <div>
              <div className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
                Model Language
              </div>
              <div className="grid grid-cols-4 gap-1.5" role="group" aria-label="Language">
                {[
                  { id: 'en', label: 'English' },
                  { id: 'ur', label: 'اردو (Urdu)' },
                  { id: 'ru', label: 'Русский' },
                  { id: 'bilingual', label: 'Bilingual' },
                ].map((lang) => (
                  <button
                    key={lang.id}
                    type="button"
                    onClick={() => setLanguage(lang.id)}
                    aria-pressed={language === lang.id}
                    className={`text-xs py-1.5 font-semibold rounded-[7px] border transition-all ${
                      language === lang.id
                        ? 'bg-[#edf5f3] text-[#0f766e] border-[#c4e3dc]'
                        : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                    }`}
                  >
                    {lang.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Export Format */}
            <div>
              <div className="block text-[11px] font-mono font-bold uppercase tracking-[0.06em] text-[#526173] mb-1.5">
                Target Export Format
              </div>
              <div className="grid grid-cols-3 gap-1.5" role="group" aria-label="Export format">
                {[
                  { id: 'txt', label: 'TXT File' },
                  { id: 'srt', label: 'SRT Subtitles' },
                  { id: 'pdf', label: 'PDF Report' },
                ].map((fmt) => (
                  <button
                    key={fmt.id}
                    type="button"
                    onClick={() => setExportFormat(fmt.id)}
                    aria-pressed={exportFormat === fmt.id}
                    className={`text-xs py-1.5 font-semibold rounded-[7px] border transition-all ${
                      exportFormat === fmt.id
                        ? 'bg-[#0f172a] text-white border-[#0f172a]'
                        : 'bg-white text-[#526173] border-[#d4dee4] hover:bg-[#eaf0f2]'
                    }`}
                  >
                    {fmt.label}
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

          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full text-xs font-semibold py-3 shadow-md"
          >
            {loading ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" />
                <span>Running Faster-Whisper Transcription...</span>
              </>
            ) : (
              <>
                <Sparkles className="w-4 h-4" />
                <span>Execute Acoustic Transcription</span>
              </>
            )}
          </button>
        </form>
      </div>

      {/* Active Job Progress */}
      {activeJob && activeJob.status !== 'completed' && activeJob.status !== 'failed' && (
        <div className="theme-card p-5 border-l-4 border-l-[#0f766e]" role="status" aria-live="polite">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2 text-xs font-semibold text-[#0f172a]">
              <Loader2 className="w-4 h-4 text-[#0f766e] animate-spin" />
              <span>{activeJob.stage || 'Transcribing speech to text...'}</span>
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

      {/* Transcript Results Viewer */}
      {result && (
        <div className="theme-card p-6 space-y-4 text-left">
          {isSimulation && (
            <div className="p-3 rounded-[9px] bg-[#fffbeb] border border-[#fde68a] text-[#b45309] text-xs flex items-start gap-2" role="status">
              <Info className="w-4 h-4 shrink-0 mt-0.5" />
              <span>
                Simulation mode: this transcript is placeholder text, not transcription of your media.
                The server needs ENABLE_HEAVY_RENDERING=1 with the Whisper pipeline for real output.
              </span>
            </div>
          )}

          {/* Action Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3 pb-3 border-b border-[#d4dee4]">
            <div>
              <h3 className="font-bold text-sm text-[#0f172a]">
                Timestamped Dialogue Log
              </h3>
              <p className="text-[11px] text-[#526173] font-mono">
                Language Model: <span className="font-bold uppercase text-[#0f766e]">{result.language || '—'}</span>
              </p>
            </div>

            {/* Search & Export Buttons */}
            <div className="flex items-center gap-2">
              <div className="relative">
                <Search className="w-3 h-3 text-[#64748b] absolute left-2.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Filter dialogue..."
                  aria-label="Filter transcript dialogue"
                  className="input-field min-h-[32px] text-xs py-1 pl-8 w-36 sm:w-48"
                />
              </div>

              <button
                type="button"
                onClick={handleCopy}
                className="btn-secondary min-h-[32px] text-xs py-1 px-2.5 flex items-center gap-1 font-medium"
                title="Copy Full Text"
              >
                {copied ? <Check className="w-3 h-3 text-[#0f766e]" /> : <Copy className="w-3 h-3" />}
                <span>{copied ? 'Copied' : 'Copy'}</span>
              </button>

              {exportHref ? (
                <a
                  href={resolveApiUrl(exportHref)}
                  download
                  className="btn-primary min-h-[32px] text-xs py-1 px-3 flex items-center gap-1 font-semibold"
                >
                  <Download className="w-3 h-3" />
                  <span className="uppercase">{exportFormat}</span>
                </a>
              ) : (
                <button
                  type="button"
                  disabled
                  className="min-h-[32px] text-xs py-1 px-3 rounded-full border border-[#d4dee4] bg-[#eaf0f2] text-[#64748b] flex items-center gap-1 font-semibold cursor-not-allowed"
                >
                  <Info className="w-3 h-3" />
                  <span className="uppercase">No file</span>
                </button>
              )}
            </div>
          </div>

          {/* Segment List */}
          <div className="space-y-2 max-h-[460px] overflow-y-auto pr-1">
            {filteredSegments.length > 0 ? (
              filteredSegments.map((seg, idx) => (
                <div
                  key={idx}
                  className="p-3 rounded-[6px] bg-[#f7fafb] border border-[#e2e8f0] hover:bg-white hover:border-[#d4dee4] transition-all flex flex-col sm:flex-row sm:items-start gap-2.5"
                >
                  <div className="font-mono text-[11px] font-semibold text-[#0f766e] bg-[#edf5f3] px-2 py-0.5 rounded-[4px] border border-[#c4e3dc] shrink-0 tabular-nums">
                    {seg.start} - {seg.end}
                  </div>
                  <div className="flex-1">
                    <div className="text-[10px] font-mono font-bold text-[#64748b] uppercase">{seg.speaker || 'Unknown speaker'}</div>
                    <p className="text-xs text-[#1e293b] leading-relaxed mt-0.5">{seg.text}</p>
                  </div>
                </div>
              ))
            ) : (
              <div className="text-center py-6 text-[#64748b] text-xs font-mono">
                No matching dialogue found for "{searchQuery}".
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
