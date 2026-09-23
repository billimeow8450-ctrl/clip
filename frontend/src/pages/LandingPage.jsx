import React, { useState } from 'react';
import confetti from 'canvas-confetti';
import {
  Sparkles, Scissors, Play, Download, CheckCircle2, Zap, Upload,
  ArrowRight, Share2, ChevronDown, Loader2, TrendingUp, Users, Eye,
  Captions, Sliders, Clock, Info
} from 'lucide-react';
import YoutubeIcon from '../components/common/YoutubeIcon';
import { api, resolveApiUrl } from '../api';
import { useJobPolling } from '../hooks/useJobPolling';

const sampleClips = [
  {
    isDemo: true,
    id: 'clip-1',
    score: 99,
    category: 'Monologue Hooks',
    title: 'The $100M Habit Nobody Talks About',
    timecode: '04:12 - 04:54',
    duration: '0:42',
    hookScore: '99%',
    retention: '3s Peak Retention',
    shareRate: 'Top 1% Shareability',
    speakerTracking: '98%',
    captionHighlight: 'YOU CANNOT LOSE IF YOU NEVER QUIT 🔥',
    captionSnippet: [
      { text: 'YOU CANNOT', highlight: false },
      { text: 'LOSE', highlight: true, color: 'text-blue-700 bg-blue-100/80 font-bold' },
      { text: 'IF YOU NEVER', highlight: false },
      { text: 'QUIT 🔥', highlight: true, color: 'text-amber-800 bg-amber-100 font-bold underline decoration-2' }
    ],
    videoUrl: 'https://assets.mixkit.co/videos/preview/mixkit-man-holding-a-microphone-in-front-of-a-laptop-42999-large.mp4',
    bgImage: 'https://images.unsplash.com/photo-1590602847861-f357a9332bbc?auto=format&fit=crop&w=600&q=80',
    type: 'single'
  },
  {
    isDemo: true,
    id: 'clip-2',
    score: 96,
    category: 'Monologue Hooks',
    title: 'Why Dopamine Fasting Actually Works',
    timecode: '12:40 - 13:38',
    duration: '0:58',
    hookScore: '96%',
    retention: '88% Expected Retention',
    shareRate: 'Viral Pick',
    speakerTracking: '95%',
    captionHighlight: 'YOUR DOPAMINE IS DEPLETED BEFORE 9 AM 🧠',
    captionSnippet: [
      { text: 'YOUR DOPAMINE IS', highlight: false },
      { text: 'DEPLETED', highlight: true, color: 'text-rose-700 bg-rose-100 font-extrabold' },
      { text: 'BEFORE 9 AM 🧠', highlight: false }
    ],
    videoUrl: 'https://assets.mixkit.co/videos/preview/mixkit-man-working-on-his-laptop-308-large.mp4',
    bgImage: 'https://images.unsplash.com/photo-1507679799987-c73779587ccf?auto=format&fit=crop&w=600&q=80',
    type: 'single'
  },
  {
    isDemo: true,
    id: 'clip-3',
    score: 92,
    category: 'Multi-Speaker Debate',
    title: 'AI Will Replace 80% of Routine Coding',
    timecode: '28:05 - 28:40',
    duration: '0:35',
    hookScore: '92%',
    retention: 'Debate Velocity',
    shareRate: 'High Comment Magnet',
    speakerTracking: 'Split Screen 360°',
    captionHighlight: 'AI WILL REPLACE 80% OF ROUTINE CODING ⚡',
    captionSnippet: [
      { text: 'AI WILL REPLACE', highlight: false },
      { text: '80%', highlight: true, color: 'text-blue-700 bg-blue-100 font-black' },
      { text: 'OF ROUTINE CODING ⚡', highlight: false }
    ],
    videoUrl: 'https://assets.mixkit.co/videos/preview/mixkit-two-business-partners-talking-about-work-in-an-office-42774-large.mp4',
    bgImage: 'https://images.unsplash.com/photo-1522071820081-009f0129c71c?auto=format&fit=crop&w=600&q=80',
    type: 'split'
  },
  {
    isDemo: true,
    id: 'clip-4',
    score: 89,
    category: 'Monologue Hooks',
    title: 'How We Scaled From $0 to $10M ARR',
    timecode: '41:12 - 41:59',
    duration: '0:47',
    hookScore: '89%',
    retention: 'Case Study Hook',
    shareRate: 'High Bookmark Rate',
    speakerTracking: '94%',
    captionHighlight: 'HOW WE HIT $10M ARR WITH 3 PEOPLE 🚀',
    captionSnippet: [
      { text: 'HOW WE HIT', highlight: false },
      { text: '$10M ARR', highlight: true, color: 'text-amber-800 bg-amber-100 font-black' },
      { text: 'WITH 3 PEOPLE 🚀', highlight: false }
    ],
    videoUrl: 'https://assets.mixkit.co/videos/preview/mixkit-woman-speaking-at-a-conference-42880-large.mp4',
    bgImage: 'https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?auto=format&fit=crop&w=600&q=80',
    type: 'single'
  }
];

export default function LandingPage({ setActiveTab, onOpenAuth }) {
  const [url, setUrl] = useState('');
  const [file, setFile] = useState(null);
  const [loading, setLoading] = useState(false);
  const [clips, setClips] = useState(sampleClips);
  const [activeFilter, setActiveFilter] = useState('all');
  const [error, setError] = useState('');
  const [simNotice, setSimNotice] = useState(false);
  const [openFaq, setOpenFaq] = useState(null);
  const [selectedClip, setSelectedClip] = useState(null);

  const demoPresets = [
    { label: '🧠 Huberman Dopamine', url: 'https://www.youtube.com/watch?v=QmOF0crdyRU', title: 'Huberman Lab: Dopamine & Motivation' },
    { label: '🎙️ Rogan & Altman #2054', url: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', title: 'Joe Rogan & Sam Altman: The AI Revolution' },
    { label: '🚀 All-In Debate', url: 'https://www.youtube.com/watch?v=jNQXAC9IVRw', title: 'All-In Summit: Economic Supercycles' },
    { label: '⚡ MrBeast Viral Secrets', url: 'https://www.youtube.com/watch?v=9bZkp7q19f0', title: 'MrBeast: The Science of High Retention' },
  ];

  const triggerCelebration = () => {
    try {
      confetti({
        particleCount: 60,
        spread: 70,
        origin: { y: 0.65 },
        colors: ['#0066ff', '#38bdf8', '#ffffff', '#fbbf24']
      });
    } catch {
      /* fallback */
    }
  };

  const handleJobFinished = (res) => {
    if (res.status === 'completed') {
      if (res.result_data?.clips && res.result_data.clips.length > 0) {
        // The clipper API returns signed `video_url` paths. The marketing
        // landing page uses the camelCase `videoUrl` field for its preview
        // modal, so normalize real API results here instead of rendering an
        // empty video element / "#" preview link.
        setClips(res.result_data.clips.map((clip) => {
          const mediaUrl = resolveApiUrl(clip.video_url);
          return {
            ...clip,
            videoUrl: mediaUrl,
            download_url: mediaUrl,
            timecode: typeof clip.start_time === 'number' && typeof clip.end_time === 'number'
              ? `${new Date(clip.start_time * 1000).toISOString().slice(14, 19)} - ${new Date(clip.end_time * 1000).toISOString().slice(14, 19)}`
              : undefined,
          };
        }));
      }
      setSimNotice(Boolean(res.result_data?.is_simulation));
      triggerCelebration();
      setLoading(false);
      const el = document.getElementById('studio');
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    } else {
      setError(res.error_message || 'Clipper processing failed.');
      setLoading(false);
      const el = document.getElementById('clipper-error');
      if (el) el.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const { activeJob, startPolling } = useJobPolling(handleJobFinished);

  const handleGenerateClips = async (overrideUrl) => {
    setError('');
    const targetUrl = (overrideUrl || url).trim();

    if (!targetUrl && !file) {
      setError('Please paste a YouTube link or drop a video file.');
      return;
    }

    // Authentication is required up front — the old auto-created guest
    // account with a hardcoded password was removed (finding C5).
    if (!api.auth.getCurrentUser()) {
      setError('Please sign in or create a free account to generate clips.');
      onOpenAuth?.();
      return;
    }

    setLoading(true);

    try {
      let finalUrl = targetUrl;
      let title = 'Viral Clip Project';

      if (file && !targetUrl) {
        const uploadRes = await api.files.upload(file);
        finalUrl = uploadRes.url_base || uploadRes.url;
        title = file.name;
      }

      const res = await api.clipper.process({
        url: finalUrl,
        title: title,
        // The landing page should return a first playable clip quickly. The
        // dedicated Clipper page still exposes longer/deeper processing.
        analysis_mode: 'quick',
        target_duration: '30',
      });

      startPolling({
        id: res.job_id,
        status: 'queued',
        progress: 10,
        stage: 'Audio Extraction & Diarization'
      });
    } catch (err) {
      setError(err.message || 'Failed to start AI clipping engine.');
      setLoading(false);
    }
  };

  const filteredClips = clips.filter((clip) => {
    if (activeFilter === '90') return (clip.score || clip.virality_score || 90) >= 90;
    if (activeFilter === 'monologue') return clip.category === 'Monologue Hooks' || clip.type === 'single';
    if (activeFilter === 'debate') return clip.category === 'Multi-Speaker Debate' || clip.type === 'split';
    return true;
  });

  return (
    <div className="space-y-24 md:space-y-32 pb-24 text-ink-body">
      {/* ===================== HERO SECTION (AI CLIPPER × REFERENCE UI) ===================== */}
      <section className="hero-mesh-bg min-h-[92vh] flex flex-col justify-center relative overflow-hidden pt-12 md:pt-20 pb-16">
        <div className="container-custom relative z-10 max-w-5xl mx-auto text-center">
          {/* Main Headline — Adapted for AI Clipper */}
          <h1 className="animate-fadeInUp font-display text-5xl sm:text-7xl lg:text-[82px] font-black text-slate-950 tracking-tight leading-[1.06] mb-6 max-w-4xl mx-auto">
            Turn Long Videos into <br />
            Viral Shorts with AI
          </h1>

          {/* Subtitle */}
          <p className="animate-fadeInUp text-base sm:text-xl text-slate-600 max-w-2xl mx-auto leading-relaxed mb-10 font-normal">
            Transform podcasts, YouTube streams, and interviews into ready-to-publish vertical shorts with AI-powered highlight detection.
          </p>

          {/* Floating Capsule Input Console (The Clipper Engine) */}
          <div className="animate-fadeInUp w-full max-w-3xl mx-auto p-2 pl-5 sm:pl-6 rounded-full bg-white/95 backdrop-blur-xl border border-slate-200/90 shadow-[0_16px_40px_-10px_rgba(0,0,0,0.07)] flex items-center justify-between gap-3 mb-6 hover:border-slate-300 transition-all">
            <div className="flex items-center gap-3 w-full">
              <YoutubeIcon className="w-6 h-6 text-rose-500 shrink-0" />
              <input
                aria-label="Video link"
                type="url"
                value={url}
                onChange={(e) => { setUrl(e.target.value); setFile(null); }}
                placeholder="Paste a YouTube, Vimeo, Twitch, or Rumble link..."
                className="w-full bg-transparent border-0 text-slate-900 placeholder:text-slate-400 text-sm sm:text-base focus:ring-0 focus:outline-none py-2 font-medium"
              />
            </div>

            {/* File upload input badge */}
            <label className="hidden sm:flex items-center gap-1.5 text-xs font-semibold px-3 py-2 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-600 cursor-pointer transition-colors shrink-0">
              <Upload className="w-3.5 h-3.5" />
              <span>{file ? file.name.slice(0, 10) + '...' : 'Drop video'}</span>
              <input
                type="file"
                accept="video/*"
                className="hidden"
                onChange={(e) => {
                  if (e.target.files && e.target.files[0]) {
                    setFile(e.target.files[0]);
                    setUrl('');
                  }
                }}
              />
            </label>

            {/* Primary Action Pill */}
            <button
              onClick={() => handleGenerateClips()}
              disabled={loading}
              className="px-6 sm:px-8 py-3 rounded-full bg-slate-950 hover:bg-slate-800 text-white text-xs sm:text-sm font-semibold transition-all shadow-sm shrink-0 hover:scale-[1.02] active:scale-[0.98] cursor-pointer flex items-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin text-white" />
                  <span>Analyzing Hooks...</span>
                </>
              ) : (
                <>
                <Zap className="w-4 h-4 fill-white" />
                <span>Get Viral Clips</span>
                </>
              )}
            </button>
          </div>

          {/* Preset Category Pills (Podcasts, Interviews, Debates, Viral Hooks) */}
          <div className="animate-fadeInUp flex flex-wrap items-center justify-center gap-2.5 mb-14">
            <span className="text-xs font-semibold text-slate-500 mr-1 hidden sm:inline">Try samples:</span>
            {demoPresets.map((demo) => (
              <button
                key={demo.label}
                onClick={() => {
                  setUrl(demo.url);
                  setFile(null);
                  handleGenerateClips(demo.url);
                }}
                className="px-5 py-2 rounded-full bg-slate-100/90 hover:bg-slate-200/90 text-slate-700 text-xs font-semibold border border-slate-200/70 shadow-2xs transition-all hover:scale-105 active:scale-95 cursor-pointer flex items-center gap-1.5"
              >
                <span>{demo.label}</span>
              </button>
            ))}
            <div className="px-3.5 py-1.5 rounded-full bg-blue-50 border border-blue-200 text-blue-700 text-xs font-semibold flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-[#0066ff] pulse-blue-dot"></span>
              <span>Free tier available — no credit card required</span>
            </div>
          </div>

          {/* Job feedback belongs next to the action that started it. Keeping it
              below the marketing sections made a working job look like a dead
              button and hid actionable failures from the user. */}
          <div className="max-w-3xl mx-auto space-y-4 text-left" aria-live="polite">
            {error && (
              <div id="clipper-error" className="p-4 rounded-xl bg-rose-50 border border-rose-200 text-rose-800 text-sm font-medium animate-fadeIn" role="alert">
                {error}
              </div>
            )}

            {activeJob && activeJob.status !== 'completed' && activeJob.status !== 'failed' && (
              <div className="p-6 rounded-2xl bg-white border border-blue-300 shadow-card animate-fadeIn" role="status">
                <div className="flex items-center justify-between mb-3 gap-4">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center shrink-0">
                      <Loader2 className="w-4 h-4 text-[#0066ff] animate-spin" />
                    </div>
                    <div className="min-w-0">
                      <h4 className="text-sm font-bold text-ink truncate">{activeJob.stage || 'Processing video...'}</h4>
                      <p className="text-xs text-ink-muted">Creating real vertical clips from your source</p>
                    </div>
                  </div>
                  <span className="text-sm font-mono font-bold text-blue-700 shrink-0">{activeJob.progress || 0}%</span>
                </div>
                <div className="w-full h-2 rounded-full bg-slate-100 overflow-hidden border border-line">
                  <div className="h-full bg-gradient-to-r from-[#0066ff] to-[#0052cc] rounded-full transition-all duration-500 ease-out" style={{ width: `${activeJob.progress || 0}%` }} />
                </div>
              </div>
            )}
          </div>

          {/* Trusted Creator Networks Bar */}
          <div className="border-t border-slate-100/90 pt-10 max-w-4xl mx-auto">
            <p className="text-xs font-mono font-semibold text-slate-400 uppercase tracking-wider mb-5">
              Built for creators, podcast networks & digital agencies
            </p>
            <div className="flex flex-wrap items-center justify-center gap-x-8 gap-y-3 text-sm font-semibold text-slate-600">
              <span>YouTube</span>
              <span aria-hidden="true">•</span>
              <span>Vimeo</span>
              <span aria-hidden="true">•</span>
              <span>Twitch</span>
              <span aria-hidden="true">•</span>
              <span>Rumble</span>
              <span aria-hidden="true">•</span>
              <span>Direct uploads</span>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== SECTION: LET YOUR CLIPS DO THE TALKING ===================== */}
      <section className="py-20 md:py-28" id="features">
        <div className="container-custom">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-3xl sm:text-5xl font-display font-bold text-slate-950 tracking-tight mb-4">
              Let Your Clips Do the Talking
            </h2>
            <p className="text-base sm:text-lg text-slate-500 leading-relaxed font-normal">
              Make a powerful impression with short-form videos that speak louder than words, conveying your brand's message effortlessly across every platform.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
            {/* Card 1: 9:16 Smart Auto-Reframe */}
            <div
              onClick={() => {
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="group rounded-3xl bg-white border border-slate-100 p-8 shadow-[0_10px_30px_rgba(0,0,0,0.03)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.06)] hover:-translate-y-1.5 transition-all duration-300 flex flex-col justify-between cursor-pointer"
            >
              <div className="w-full h-56 rounded-2xl overflow-hidden bg-slate-900 mb-6 flex items-center justify-center relative group-hover:scale-[1.02] transition-transform">
                <img
                  src="https://images.unsplash.com/photo-1590602847861-f357a9332bbc?auto=format&fit=crop&w=800&q=80"
                  alt="9:16 Smart Reframe"
                  className="w-full h-full object-cover brightness-90 group-hover:scale-105 transition-transform duration-700"
                />
                {/* Active speaker face tracking HUD simulation */}
                <div className="absolute inset-x-8 inset-y-6 border-2 border-[#0066ff] rounded-xl flex flex-col justify-between p-2 pointer-events-none">
                  <div className="flex items-center justify-between text-[10px] font-mono text-white bg-black/80 px-2 py-0.5 rounded">
                    <span>AI FACE TRACKING</span>
                  <span className="text-blue-400 font-bold">Preview</span>
                  </div>
                  <div className="self-center px-2 py-0.5 rounded bg-blue-600 text-white font-mono text-[10px] font-bold">
                    9:16 VERTICAL LOCK
                  </div>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-2xl font-display font-bold text-slate-950">9:16 Smart Reframe</h3>
                  <div className="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center text-slate-950 group-hover:bg-slate-950 group-hover:text-white transition-colors">
                    <ArrowRight className="w-5 h-5" />
                  </div>
                </div>
                <p className="text-sm text-slate-500 leading-relaxed">
                  Computer vision tracks active faces dynamically, reframing widescreen videos into portrait 9:16 without manual keyframes.
                </p>
              </div>
            </div>

            {/* Card 2: Predictive Virality Score™ */}
            <div
              onClick={() => {
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="group rounded-3xl bg-white border border-slate-100 p-8 shadow-[0_10px_30px_rgba(0,0,0,0.03)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.06)] hover:-translate-y-1.5 transition-all duration-300 flex flex-col justify-between cursor-pointer"
            >
              <div className="w-full h-56 rounded-2xl overflow-hidden bg-slate-950 mb-6 flex flex-col justify-between p-5 relative group-hover:scale-[1.02] transition-transform">
                <div className="flex items-center justify-between">
                  <div className="px-3 py-1 rounded-full bg-blue-500/20 border border-blue-400/40 text-blue-300 text-xs font-mono font-bold">
                    PREDICTIVE VIRALITY
                  </div>
                  <span className="text-2xl font-black text-white font-mono">99/100</span>
                </div>

                {/* Simulated Retention Curve Graphic */}
                <div className="h-24 flex items-end gap-1.5 pt-2">
                  {[95, 92, 98, 94, 89, 86, 92, 96, 99, 94, 91, 95].map((v, i) => (
                    <div
                      key={i}
                      style={{ height: `${v}%` }}
                      className="w-full bg-gradient-to-t from-blue-600 via-blue-400 to-sky-300 rounded-t"
                    />
                  ))}
                </div>

                <div className="flex items-center justify-between text-[10px] font-mono text-slate-400 border-t border-slate-800 pt-2">
                  <span>0s Hook Velocity</span>
                  <span className="text-blue-400 font-bold">Peak Watch-Through</span>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-2xl font-display font-bold text-slate-950">Predictive Virality Score™</h3>
                  <div className="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center text-slate-950 group-hover:bg-slate-950 group-hover:text-white transition-colors">
                    <ArrowRight className="w-5 h-5" />
                  </div>
                </div>
                <p className="text-sm text-slate-500 leading-relaxed">
                  Our neural engine evaluates speech cadence, narrative payoff, and initial 3s hook velocity to forecast watch-through rates.
                </p>
              </div>
            </div>

            {/* Card 3: Kinetic Auto Captions */}
            <div
              onClick={() => {
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="group rounded-3xl bg-white border border-slate-100 p-8 shadow-[0_10px_30px_rgba(0,0,0,0.03)] hover:shadow-[0_20px_40px_rgba(0,0,0,0.06)] hover:-translate-y-1.5 transition-all duration-300 flex flex-col justify-between cursor-pointer"
            >
              <div className="w-full h-56 rounded-2xl overflow-hidden bg-gradient-to-br from-slate-900 via-slate-950 to-black mb-6 flex flex-col items-center justify-center p-6 text-center relative group-hover:scale-[1.02] transition-transform">
                <div className="p-3.5 rounded-xl bg-white/95 backdrop-blur-md border border-slate-200 shadow-md">
                  <p className="font-display font-black text-sm text-slate-950 uppercase tracking-tight">
                    "YOU CANNOT <span className="text-blue-700 bg-blue-100 px-1 rounded font-black">LOSE</span> IF YOU NEVER <span className="text-amber-800 bg-amber-100 px-1 rounded font-black underline">QUIT</span> 🔥"
                  </p>
                </div>
                <div className="mt-4 flex items-center gap-2 text-[10px] font-mono text-slate-400">
                  <span>Caption preview</span>
                  <span>•</span>
                  <span>Editable styles</span>
                  <span>•</span>
                  <span>Auto Emojis</span>
                </div>
              </div>

              <div>
                <div className="flex items-center justify-between mb-2">
                  <h3 className="text-2xl font-display font-bold text-slate-950">Kinetic Auto Captions</h3>
                  <div className="w-10 h-10 rounded-full bg-slate-50 flex items-center justify-center text-slate-950 group-hover:bg-slate-950 group-hover:text-white transition-colors">
                    <ArrowRight className="w-5 h-5" />
                  </div>
                </div>
                <p className="text-sm text-slate-500 leading-relaxed">
                  Caption layouts, timing previews, and export-ready transcript formats for a practical editing workflow.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== SECTION: EXPLORE THE LATEST VIRAL FORMATS ===================== */}
      <section className="py-20 md:py-28" id="formats">
        <div className="container-custom">
          <div className="text-center max-w-2xl mx-auto mb-16">
            <h2 className="text-3xl sm:text-5xl font-display font-bold text-slate-950 tracking-tight mb-4">
              Explore the Latest Viral Formats
            </h2>
            <p className="text-base sm:text-lg text-slate-500 leading-relaxed font-normal">
              Explore a set of editing concepts for the formats your team can build in the studio.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {/* Format Poster 1: The Monologue Hook */}
            <div
              onClick={() => {
                setActiveFilter('monologue');
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="relative aspect-[3/4] rounded-3xl overflow-hidden bg-slate-950 p-6 flex flex-col justify-between group cursor-pointer shadow-lg hover:shadow-2xl hover:scale-[1.02] transition-all duration-300"
            >
              <img
                src="https://images.unsplash.com/photo-1590602847861-f357a9332bbc?auto=format&fit=crop&w=800&q=80"
                alt="Monologue Hook"
                className="absolute inset-0 w-full h-full object-cover brightness-60 group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-950/40 to-transparent pointer-events-none" />

              <div className="relative z-10 flex justify-between items-start">
                <span className="text-xs font-mono font-black text-white px-2.5 py-1 bg-[#0066ff] rounded-full shadow-xs">
                  99 VIRALITY
                </span>
                <span className="text-xs font-mono text-white/90 bg-black/60 px-2 py-0.5 rounded">0:45</span>
              </div>

              <div className="relative z-10">
                <span className="text-[11px] font-mono text-blue-300 uppercase tracking-wider font-bold">FORMAT 01</span>
                <h4 className="text-xl font-bold text-white tracking-tight leading-snug mt-1">
                  The Monologue Hook
                </h4>
                <p className="text-xs text-slate-300 mt-1 line-clamp-2">
                  Solo high-retention podcast moments reframed to vertical.
                </p>
              </div>
            </div>

            {/* Format Poster 2: Split-Screen Debate */}
            <div
              onClick={() => {
                setActiveFilter('debate');
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="relative aspect-[3/4] rounded-3xl overflow-hidden bg-slate-950 p-6 flex flex-col justify-between group cursor-pointer shadow-lg hover:shadow-2xl hover:scale-[1.02] transition-all duration-300"
            >
              <img
                src="https://images.unsplash.com/photo-1577563908411-5077b6dc7624?auto=format&fit=crop&w=800&q=80"
                alt="Multi-Speaker Debate"
                className="absolute inset-0 w-full h-full object-cover brightness-60 group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-950/40 to-transparent pointer-events-none" />

              <div className="relative z-10 flex justify-between items-start">
                <span className="text-xs font-mono font-black text-white px-2.5 py-1 bg-amber-600 rounded-full shadow-xs">
                  96 VIRALITY
                </span>
                <span className="text-xs font-mono text-white/90 bg-black/60 px-2 py-0.5 rounded">0:58</span>
              </div>

              <div className="relative z-10">
                <span className="text-[11px] font-mono text-amber-300 uppercase tracking-wider font-bold">FORMAT 02</span>
                <h4 className="text-xl font-bold text-white tracking-tight leading-snug mt-1">
                  Multi-Speaker Debate
                </h4>
                <p className="text-xs text-slate-300 mt-1 line-clamp-2">
                  Two-speaker auto-swapping camera focus for fiery clashes.
                </p>
              </div>
            </div>

            {/* Format Poster 3: Cyber & Tech Breakdown */}
            <div
              onClick={() => {
                setActiveFilter('90');
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="relative aspect-[3/4] rounded-3xl overflow-hidden bg-slate-950 p-6 flex flex-col justify-between group cursor-pointer shadow-lg hover:shadow-2xl hover:scale-[1.02] transition-all duration-300"
            >
              <img
                src="https://images.unsplash.com/photo-1526374965328-7f61d4dc18c5?auto=format&fit=crop&w=800&q=80"
                alt="Tech Breakdown"
                className="absolute inset-0 w-full h-full object-cover brightness-60 group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-950/40 to-transparent pointer-events-none" />

              <div className="relative z-10 flex justify-between items-start">
                <span className="text-xs font-mono font-black text-white px-2.5 py-1 bg-cyan-600 rounded-full shadow-xs">
                  95 VIRALITY
                </span>
                <span className="text-xs font-mono text-white/90 bg-black/60 px-2 py-0.5 rounded">0:49</span>
              </div>

              <div className="relative z-10">
                <span className="text-[11px] font-mono text-cyan-300 uppercase tracking-wider font-bold">FORMAT 03</span>
                <h4 className="text-xl font-bold text-white tracking-tight leading-snug mt-1">
                  Tech Breakdown
                </h4>
                <p className="text-xs text-slate-300 mt-1 line-clamp-2">
                  Fast-paced visual tutorials with diagram and HUD overlays.
                </p>
              </div>
            </div>

            {/* Format Poster 4: Storytelling Payoff */}
            <div
              onClick={() => {
                setActiveFilter('all');
                const el = document.getElementById('studio');
                if (el) el.scrollIntoView({ behavior: 'smooth' });
              }}
              className="relative aspect-[3/4] rounded-3xl overflow-hidden bg-slate-950 p-6 flex flex-col justify-between group cursor-pointer shadow-lg hover:shadow-2xl hover:scale-[1.02] transition-all duration-300"
            >
              <img
                src="https://images.unsplash.com/photo-1516251193007-45ef944ab0c6?auto=format&fit=crop&w=800&q=80"
                alt="Storytelling Payoff"
                className="absolute inset-0 w-full h-full object-cover brightness-60 group-hover:scale-105 transition-transform duration-500"
              />
              <div className="absolute inset-0 bg-gradient-to-t from-slate-950 via-slate-950/40 to-transparent pointer-events-none" />

              <div className="relative z-10 flex justify-between items-start">
                <span className="text-xs font-mono font-black text-white px-2.5 py-1 bg-purple-600 rounded-full shadow-xs">
                  94 VIRALITY
                </span>
                <span className="text-xs font-mono text-white/90 bg-black/60 px-2 py-0.5 rounded">0:52</span>
              </div>

              <div className="relative z-10">
                <span className="text-[11px] font-mono text-purple-300 uppercase tracking-wider font-bold">FORMAT 04</span>
                <h4 className="text-xl font-bold text-white tracking-tight leading-snug mt-1">
                  Storytelling Payoff
                </h4>
                <p className="text-xs text-slate-300 mt-1 line-clamp-2">
                  High-suspense narrative arcs with emotional climax pacing.
                </p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== INTERACTIVE CLIPPING STUDIO SHOWCASE ===================== */}
      <section className="py-20 md:py-28 bg-white border-y border-line relative" id="studio">
        <div className="container-custom">
          {/* Section Header */}
          <div className="flex flex-col md:flex-row md:items-end justify-between mb-12 gap-6">
            <div>
              <div className="inline-flex items-center gap-2 text-blue-700 text-xs font-mono font-bold tracking-wider uppercase mb-2">
                <TrendingUp className="w-4 h-4" />
                PREDICTIVE VIRALITY ENGINE
              </div>
              <h2 className="text-3xl sm:text-4xl font-display font-bold text-ink tracking-tight">
                Clip concepts and recent results
              </h2>
            </div>

            {/* Filters */}
            <div className="flex items-center gap-2 overflow-x-auto pb-2 md:pb-0">
              <button
                onClick={() => setActiveFilter('all')}
                className={`px-4 py-2 rounded-full text-xs font-semibold whitespace-nowrap transition-colors ${
                  activeFilter === 'all'
                    ? 'bg-blue-50 text-blue-700 border border-blue-300 shadow-xs'
                    : 'bg-white text-ink-muted hover:text-ink border border-line'
                }`}
              >
                All ({clips.length})
              </button>
              <button
                onClick={() => setActiveFilter('90')}
                className={`px-4 py-2 rounded-full text-xs font-semibold whitespace-nowrap transition-colors ${
                  activeFilter === '90'
                    ? 'bg-blue-50 text-blue-700 border border-blue-300'
                    : 'bg-white text-ink-muted hover:text-ink border border-line'
                }`}
              >
                Virality Score 90+ ({clips.filter((c) => (c.score || c.viral_score || 0) >= 90).length})
              </button>
              <button
                onClick={() => setActiveFilter('monologue')}
                className={`px-4 py-2 rounded-full text-xs font-semibold whitespace-nowrap transition-colors ${
                  activeFilter === 'monologue'
                    ? 'bg-blue-50 text-blue-700 border border-blue-300'
                    : 'bg-white text-ink-muted hover:text-ink border border-line'
                }`}
              >
                Monologue Hooks
              </button>
              <button
                onClick={() => setActiveFilter('debate')}
                className={`px-4 py-2 rounded-full text-xs font-semibold whitespace-nowrap transition-colors ${
                  activeFilter === 'debate'
                    ? 'bg-blue-50 text-blue-700 border border-blue-300'
                    : 'bg-white text-ink-muted hover:text-ink border border-line'
                }`}
              >
                Multi-Speaker Debate
              </button>
            </div>
          </div>

          {(simNotice || clips.some((clip) => clip.isDemo || clip.is_sample)) && (
            <div className="mb-6 p-3 rounded-xl bg-[#fffbeb] border border-[#fde68a] text-[#b45309] text-xs flex items-start gap-2" role="status">
              <Info className="w-4 h-4 shrink-0 mt-0.5" />
              <span>Preview data is clearly marked and is not generated from your media. Real processing is available only when the server rendering pipeline is enabled.</span>
            </div>
          )}

          {/* 9:16 Vertical Cards Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6">
            {filteredClips.map((clip, idx) => (
              <div
                key={clip.id || idx}
                className="group rounded-2xl bg-white border border-line hover:border-blue-500 transition-all duration-300 p-3.5 flex flex-col shadow-card hover:shadow-card-hover"
              >
                {/* 9:16 Video Stage */}
                <div className="relative w-full aspect-[9/16] rounded-xl overflow-hidden bg-slate-900 flex flex-col justify-between p-3.5 group/video cursor-pointer">
                  {/* Background Mock */}
                  <img
                    src={clip.bgImage || sampleClips[idx % sampleClips.length].bgImage}
                    alt={clip.title}
                    className="absolute inset-0 w-full h-full object-cover brightness-90 group-hover/video:scale-105 transition-transform duration-700"
                  />

                  {/* Laser Scanning Line Animation */}
                  <div className="absolute inset-x-0 h-0.5 bg-gradient-to-r from-transparent via-[#0066ff] to-transparent shadow-[0_0_12px_#0066ff] pointer-events-none animate-scan-line z-10 opacity-75" />

                  {/* Top Overlay */}
                  <div className="relative z-10 flex items-start justify-between">
                    <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full bg-white/95 backdrop-blur-md border border-blue-200 shadow-xs">
                      <span className="w-2 h-2 rounded-full bg-[#0066ff] pulse-blue-dot"></span>
                      <span className="text-xs font-mono font-bold text-blue-800">
                        {clip.isDemo || clip.is_sample ? 'DEMO PREVIEW' : `${clip.score || clip.virality_score || 0} SCORE`}
                      </span>
                    </div>

                    <div className="flex items-center gap-1.5">
                      {/* Audio frequency wave equalizer animation */}
                      <div className="flex items-end gap-0.5 h-3 px-1.5 py-0.5 rounded bg-black/60 backdrop-blur-xs">
                        <span className="w-0.5 bg-[#0066ff] rounded-full animate-wave-bar-1"></span>
                        <span className="w-0.5 bg-[#0066ff] rounded-full animate-wave-bar-2"></span>
                        <span className="w-0.5 bg-[#0066ff] rounded-full animate-wave-bar-3"></span>
                        <span className="w-0.5 bg-[#0066ff] rounded-full animate-wave-bar-4"></span>
                      </div>
                      <span className="px-2 py-1 rounded bg-black/70 backdrop-blur-md text-[11px] font-mono text-white">
                        {clip.duration || '0:45'}
                      </span>
                    </div>
                  </div>

                  {/* Center Play Hover Overlay */}
                  <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-0 group-hover/video:opacity-100 transition-opacity duration-200 z-20">
                    <div className="w-12 h-12 rounded-full bg-[#0066ff] text-white flex items-center justify-center shadow-lg shadow-blue-500/50 backdrop-blur-xs transform scale-90 group-hover/video:scale-100 transition-transform">
                      <Play className="w-5 h-5 fill-white ml-0.5" />
                    </div>
                  </div>

                  {/* Active Speaker Frame Simulation */}
                  {clip.type === 'split' ? (
                    <div className="absolute inset-x-4 top-14 bottom-24 border border-blue-400/70 rounded-lg pointer-events-none flex flex-col justify-between p-1">
                      <div className="flex justify-between items-center text-[9px] font-mono text-blue-300 bg-black/80 px-1 rounded">
                        <span>Speaker 1</span>
                        <span>Auto-Focus</span>
                      </div>
                      <div className="flex justify-between items-center text-[9px] font-mono text-blue-300 bg-black/80 px-1 rounded">
                        <span>Speaker 2</span>
                        <span>Auto-Focus</span>
                      </div>
                    </div>
                  ) : (
                    <div className="absolute inset-x-6 top-16 bottom-32 border border-blue-400/60 rounded-xl pointer-events-none flex flex-col justify-between p-1.5">
                      <span className="text-[9px] font-mono text-blue-300 bg-black/85 px-1.5 py-0.5 rounded self-start">
                        ACTIVE SPEAKER
                      </span>
                      <span className="text-[9px] font-mono text-blue-300 bg-black/85 px-1.5 py-0.5 rounded self-end">
                        Eye Tracking: {clip.speakerTracking || '98%'}
                      </span>
                    </div>
                  )}

                  {/* Subtitle Highlight Overlay with Word Pop */}
                  <div className="relative z-10">
                    <div className="p-2.5 rounded-xl bg-white/95 backdrop-blur-md border border-line text-center shadow-md">
                      <p className="font-display font-bold text-xs sm:text-[13px] leading-snug tracking-tight text-ink uppercase">
                        {clip.captionSnippet ? (
                          clip.captionSnippet.map((chunk, i) => (
                            <span
                              key={i}
                              className={chunk.highlight ? `px-1 rounded ${chunk.color} animate-word-pop` : 'mx-0.5'}
                            >
                              {chunk.text}{' '}
                            </span>
                          ))
                        ) : (
                          clip.captionHighlight || clip.hook || 'UNREAL AI HOOK PREVIEW'
                        )}
                      </p>
                    </div>
                  </div>

                  {/* Animated scrubber timeline */}
                  <div className="absolute bottom-0 inset-x-0 h-1 bg-black/40 z-20 overflow-hidden">
                    <div className="h-full bg-gradient-to-r from-[#0066ff] to-sky-400 animate-scrub"></div>
                  </div>
                </div>

                {/* Card Metadata */}
                <div className="mt-3.5 flex flex-col flex-grow justify-between">
                  <div>
                    <div className="flex items-center justify-between text-[11px] font-mono text-ink-dim mb-1">
                      <span>Clip #{idx + 1} • {clip.timecode || '00:00 - 00:45'}</span>
                      <span className="text-blue-700 font-bold flex items-center gap-0.5">
                        <TrendingUp className="w-3.5 h-3.5" /> Top 1%
                      </span>
                    </div>
                    <h3 className="font-display font-semibold text-sm text-ink line-clamp-1 mb-2">
                      {clip.title || 'Viral Video Moment'}
                    </h3>
                    <div className="p-2 rounded-lg bg-subtle border border-line mb-3 text-xs text-ink-muted flex items-center gap-1.5">
                      <Sparkles className="w-3.5 h-3.5 text-[#0066ff] shrink-0" />
                      <span className="truncate">
                        Hook: {clip.hookScore || '98%'} • {clip.retention || '3s Peak'}
                      </span>
                    </div>
                  </div>

                  {/* Actions */}
                  <div className="grid grid-cols-2 gap-2 pt-2 border-t border-line">
                    <button
                      onClick={() => setSelectedClip(clip)}
                      className="py-2 px-3 rounded-full bg-subtle hover:bg-slate-200 text-ink text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors"
                    >
                      <Scissors className="w-3.5 h-3.5" />
                      <span>Edit Clip</span>
                    </button>
                    <a
                      href={clip.download_url || clip.videoUrl || '#'}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="btn-primary text-xs py-1.5 px-3 font-semibold"
                    >
                      <Download className="w-3.5 h-3.5" />
                      <span>{clip.download_url ? 'Export MP4' : 'Preview'}</span>
                    </a>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ===================== CORE FEATURE BENTO GRID ===================== */}
      <section className="py-20 relative" id="bento">
        <div className="container-custom">
          <div className="text-center max-w-3xl mx-auto mb-16">
            <div className="inline-flex items-center gap-2 text-gold-600 text-xs font-mono font-bold tracking-wider uppercase mb-3">
              <CheckCircle2 className="w-4 h-4" />
              ARCHITECTED FOR RETENTION
            </div>
            <h2 className="text-3xl sm:text-5xl font-display font-bold text-ink mb-4 tracking-tight">
              Computational Intelligence Behind Every Frame
            </h2>
            <p className="text-base sm:text-lg text-ink-muted">
              Clip Studio gives creators one secure workspace for source intake, clip selection, timelines, and transcript exports.
            </p>
          </div>

          {/* Bento Grid */}
          <div className="grid grid-cols-1 md:grid-cols-12 gap-6">
            {/* Bento Card 1: Large Analytical Hub (Col 7) */}
            <div className="md:col-span-7 rounded-3xl bg-white border border-line p-8 flex flex-col justify-between relative shadow-card hover:shadow-card-hover transition-shadow">
              <div>
                <div className="w-12 h-12 rounded-2xl bg-blue-50 border border-blue-200 flex items-center justify-center mb-6">
                  <TrendingUp className="w-6 h-6 text-[#0066ff]" />
                </div>
                <h3 className="text-2xl font-display font-bold text-ink mb-3">
                  AI Virality Score™ & Retention Modeling
                </h3>
                <p className="text-sm sm:text-base text-ink-muted max-w-xl mb-6 leading-relaxed">
                  Use clip candidates and transcript context to make editorial decisions. Scores are directional signals, not a prediction or guarantee of reach.
                </p>
              </div>

              {/* Visual Graph Demo */}
              <div className="w-full bg-canvas border border-line rounded-2xl p-5 shadow-inner">
                <div className="flex items-center justify-between text-xs font-mono text-ink-dim mb-3">
                  <span className="flex items-center gap-2 text-blue-700 font-semibold">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#0066ff] pulse-blue-dot"></span>
                    Retention Probability Curve (0 - 60s)
                  </span>
                  <span className="text-blue-700 font-bold">EDITORIAL PREVIEW</span>
                </div>
                {/* Simulated retention curve bars */}
                <div className="h-28 flex items-end gap-1.5 pt-4">
                  {[95, 92, 97, 94, 88, 85, 91, 78, 82, 89, 98, 93, 90, 94].map((val, i) => (
                    <div
                      key={i}
                      style={{ height: `${val}%` }}
                      className="w-full bg-gradient-to-t from-blue-200 via-blue-400 to-[#0066ff] hover:brightness-110 rounded-t transition-all"
                      title={`${val}% retention`}
                    />
                  ))}
                </div>
                <div className="flex justify-between items-center mt-3 pt-3 border-t border-line text-xs text-ink-dim font-mono">
                  <span>0s (Hook Point)</span>
                  <span className="text-blue-700 font-semibold">18s (Payload Peak)</span>
                  <span>45s (Call-to-Action)</span>
                </div>
              </div>
            </div>

            {/* Bento Card 2: Active Speaker 360° Tracking (Col 5) */}
            <div className="md:col-span-5 rounded-3xl bg-white border border-line p-8 flex flex-col justify-between relative shadow-card hover:shadow-card-hover transition-shadow">
              <div>
                <div className="w-12 h-12 rounded-2xl bg-gold-50 border border-gold-200 flex items-center justify-center mb-6">
                  <Eye className="w-6 h-6 text-gold-600" />
                </div>
                <h3 className="text-2xl font-display font-bold text-ink mb-3">
                  Active Speaker 360° Tracking
                </h3>
                <p className="text-sm sm:text-base text-ink-muted mb-6 leading-relaxed">
                  Never deal with manual keyframes again. Our computer vision tracks faces dynamically,
                  reframing widescreen videos into vertical portraits without clipping gestures.
                </p>
              </div>

              {/* Visual Crop Preview */}
              <div className="relative rounded-2xl bg-canvas p-4 border border-line flex items-center justify-center">
                <div className="w-full h-32 rounded-xl bg-white flex items-center justify-center relative overflow-hidden border border-line shadow-xs">
                  {/* Speaker Background simulation */}
                  <div className="absolute inset-0 flex items-center justify-around opacity-40 text-xs font-mono">
                    <span className="flex flex-col items-center gap-1">
                      <span className="w-8 h-8 rounded-full bg-slate-200 border border-slate-300"></span>
                      Speaker A
                    </span>
                    <span className="flex flex-col items-center gap-1">
                      <span className="w-8 h-8 rounded-full bg-slate-200 border border-slate-300"></span>
                      Speaker B
                    </span>
                  </div>

                  {/* Laser scan line across crop */}
                  <div className="absolute inset-y-0 w-0.5 bg-gradient-to-b from-transparent via-[#0066ff] to-transparent shadow-[0_0_8px_#0066ff] pointer-events-none animate-float-reverse opacity-70" />

                  {/* 9:16 Locked Smart Reframe Box with subtle float-tracking */}
                  <div className="w-24 h-[90%] bg-blue-50/90 border-2 border-[#0066ff] rounded-lg flex flex-col items-center justify-center z-10 shadow-sm animate-float-slow backdrop-blur-xs">
                    <Users className="w-5 h-5 text-[#0066ff]" />
                    <span className="text-[9px] font-bold text-blue-800 mt-1">LOCKED (9:16)</span>
                    <span className="text-[8px] font-mono text-blue-600 font-semibold">LAYOUT PREVIEW</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Bento Card 3: Dynamic Kinetic Captions (Col 6) */}
            <div className="md:col-span-6 rounded-3xl bg-white border border-line p-8 flex flex-col justify-between relative shadow-card hover:shadow-card-hover transition-shadow">
              <div>
                <div className="w-12 h-12 rounded-2xl bg-blue-50 border border-blue-200 flex items-center justify-center mb-6">
                  <Captions className="w-6 h-6 text-[#0066ff]" />
                </div>
                <h3 className="text-2xl font-display font-bold text-ink mb-3">
                  Kinetic Captions & Smart Emojis
                </h3>
                <p className="text-sm sm:text-base text-ink-muted mb-6 leading-relaxed">
                  Select a caption style, inspect the transcript, and export the text formats your editing workflow needs.
                </p>
              </div>

              <div className="p-4 rounded-2xl bg-canvas border border-line flex flex-col gap-2.5">
                <div className="flex items-center gap-2 text-xs font-mono">
                  <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-semibold">Preset: Beast Viral</span>
                  <span className="px-2 py-0.5 rounded bg-white text-ink-dim border border-line">Auto-Punctuation ON</span>
                </div>
                <div className="p-3.5 bg-white rounded-xl text-center border border-line shadow-xs">
                  <span className="font-display font-black text-sm sm:text-base text-ink tracking-wide">
                    "WE SCALED TO <span className="text-blue-700 bg-blue-50 px-1.5 py-0.5 rounded border border-blue-200">1,000,000</span> USERS IN 90 DAYS 📈"
                  </span>
                </div>
              </div>
            </div>

            {/* Bento Card 4: Multi-Platform Auto Dispatch (Col 6) */}
            <div className="md:col-span-6 rounded-3xl bg-white border border-line p-8 flex flex-col justify-between relative shadow-card hover:shadow-card-hover transition-shadow">
              <div>
                <div className="w-12 h-12 rounded-2xl bg-gold-50 border border-gold-200 flex items-center justify-center mb-6">
                  <Share2 className="w-6 h-6 text-gold-600" />
                </div>
                <h3 className="text-2xl font-display font-bold text-ink mb-3">
                  1-Click Multi-Channel Auto Dispatch
                </h3>
                <p className="text-sm sm:text-base text-ink-muted mb-6 leading-relaxed">
                  Schedule and auto-post your generated clips straight to TikTok, Instagram Reels,
                  YouTube Shorts, LinkedIn, and X with AI-generated titles, hooks, and hashtags.
                </p>
              </div>

              <div className="grid grid-cols-4 gap-2.5 p-3 rounded-2xl bg-canvas border border-line text-center">
                <div className="p-2.5 rounded-xl bg-white flex flex-col items-center gap-1.5 border border-line hover:border-blue-500 transition-colors shadow-xs">
                  <YoutubeIcon className="w-5 h-5 text-rose-500" />
                  <span className="text-xs font-semibold text-ink">Shorts</span>
                </div>
                <div className="p-2.5 rounded-xl bg-white flex flex-col items-center gap-1.5 border border-line hover:border-blue-500 transition-colors shadow-xs">
                  <Zap className="w-5 h-5 text-[#0066ff]" />
                  <span className="text-xs font-semibold text-ink">TikTok</span>
                </div>
                <div className="p-2.5 rounded-xl bg-white flex flex-col items-center gap-1.5 border border-line hover:border-blue-500 transition-colors shadow-xs">
                  <Play className="w-5 h-5 text-rose-500" />
                  <span className="text-xs font-semibold text-ink">Reels</span>
                </div>
                <div className="p-2.5 rounded-xl bg-white flex flex-col items-center gap-1.5 border border-line hover:border-blue-500 transition-colors shadow-xs">
                  <Share2 className="w-5 h-5 text-sky-600" />
                  <span className="text-xs font-semibold text-ink">LinkedIn</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== CAPABILITIES ===================== */}
      <section className="py-20 bg-white border-t border-line relative">
        <div className="container-custom">
          {/* Pipeline facts (replaces fabricated usage metrics — finding M8) */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-8 pb-16 border-b border-line">
            <div>
              <span className="font-display text-4xl sm:text-5xl font-extrabold text-ink tracking-tight">9:16</span>
              <span className="block text-sm font-semibold text-blue-700 mt-1">Vertical Auto-Reframe</span>
              <span className="text-xs text-ink-dim mt-0.5 block">Speaker-aware cropping</span>
            </div>
            <div>
              <span className="font-display text-4xl sm:text-5xl font-extrabold text-ink tracking-tight">3-in-1</span>
              <span className="block text-sm font-semibold text-gold-600 mt-1">AI Studio</span>
              <span className="text-xs text-ink-dim mt-0.5 block">Clipper, Editor, Transcriber</span>
            </div>
            <div>
              <span className="font-display text-4xl sm:text-5xl font-extrabold text-ink tracking-tight">5+</span>
              <span className="block text-sm font-semibold text-blue-700 mt-1">Source Platforms</span>
              <span className="text-xs text-ink-dim mt-0.5 block">Or direct file uploads</span>
            </div>
            <div>
              <span className="font-display text-4xl sm:text-5xl font-extrabold text-ink tracking-tight">500MB</span>
              <span className="block text-sm font-semibold text-blue-800 mt-1">Uploads per File</span>
              <span className="text-xs text-ink-dim mt-0.5 block">MP4, MOV, MKV, MP3 & more</span>
            </div>
          </div>

          {/* Workflow pillars (replaces fabricated testimonials — finding M8) */}
          <div className="pt-16">
            <div className="text-center max-w-2xl mx-auto mb-12">
              <h2 className="text-3xl font-display font-bold text-ink mb-3">
                Built for the Short-Form Workflow
              </h2>
              <p className="text-sm sm:text-base text-ink-muted">
                Everything in the studio is designed around how clipping actually gets done.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              <div className="p-6 rounded-2xl bg-canvas border border-line flex flex-col justify-between shadow-card">
                <div>
                  <div className="w-10 h-10 rounded-full bg-blue-50 border border-blue-200 flex items-center justify-center text-[#0066ff] mb-4">
                    <Zap className="w-5 h-5" />
                  </div>
                  <p className="text-sm text-ink-body leading-relaxed mb-6">
                    Paste a link or drop a file and let the analysis pipeline segment the source into candidate shorts with timestamps, hooks, and scores you can inspect.
                  </p>
                </div>
                <h4 className="text-sm font-bold text-ink">From long-form to candidates in one pass</h4>
              </div>

              <div className="p-6 rounded-2xl bg-canvas border border-line flex flex-col justify-between shadow-card">
                <div>
                  <div className="w-10 h-10 rounded-full bg-gold-50 border border-gold-200 flex items-center justify-center text-gold-600 mb-4">
                    <Sliders className="w-5 h-5" />
                  </div>
                  <p className="text-sm text-ink-body leading-relaxed mb-6">
                    Set exact start and end times on an interactive timeline, then choose caption and layout presets — the render pipeline handles reframing to 9:16.
                  </p>
                </div>
                <h4 className="text-sm font-bold text-ink">Precise control where it matters</h4>
              </div>

              <div className="p-6 rounded-2xl bg-canvas border border-line flex flex-col justify-between shadow-card">
                <div>
                  <div className="w-10 h-10 rounded-full bg-blue-50 border border-blue-200 flex items-center justify-center text-[#0066ff] mb-4">
                    <Captions className="w-5 h-5" />
                  </div>
                  <p className="text-sm text-ink-body leading-relaxed mb-6">
                    Transcripts arrive timestamped and speaker-tagged, searchable in the browser, and exportable as TXT or SRT for your caption workflow.
                  </p>
                </div>
                <h4 className="text-sm font-bold text-ink">Transcription that fits editing</h4>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== HIGH-CONVERTING BOTTOM CTA ===================== */}
      <section className="py-20 relative overflow-hidden">
        <div className="container-custom max-w-5xl mx-auto">
          <div className="relative rounded-3xl bg-gradient-to-b from-white to-blue-50/50 border border-line p-8 md:p-16 text-center shadow-card overflow-hidden">
            <div className="relative z-10 max-w-3xl mx-auto">
              <span className="px-4 py-1.5 rounded-full bg-blue-50 text-[#0066ff] border border-blue-200 text-xs font-semibold inline-flex items-center gap-2 mb-6">
                <Clock className="w-3.5 h-3.5" />
                Free to try — no credit card required
              </span>

              <h2 className="text-3xl sm:text-5xl font-display font-bold text-ink mb-6 tracking-tight">
                Ready to Make Your First AI Clip?
              </h2>

              <p className="text-base sm:text-lg text-ink-muted mb-10 max-w-2xl mx-auto">
                Create a free account, paste a YouTube link or upload a video, and the studio takes it from there.
              </p>

              <div className="flex flex-col sm:flex-row items-center justify-center gap-3 max-w-md mx-auto">
                <button
                  type="button"
                  onClick={() => onOpenAuth?.()}
                  className="btn-primary w-full sm:w-auto py-3.5 px-8 text-sm font-semibold shrink-0"
                >
                  Create Free Account
                </button>
              </div>

              <div className="mt-8 flex flex-wrap items-center justify-center gap-6 text-xs text-ink-dim">
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#0066ff]" /> No credit card required
                </span>
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#0066ff]" /> Sign up in under a minute
                </span>
                <span className="flex items-center gap-1.5">
                  <CheckCircle2 className="w-4 h-4 text-[#0066ff]" /> Transparent processing status
                </span>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* ===================== FAQ SECTION ===================== */}
      <section className="py-12 relative" id="faq">
        <div className="container-custom max-w-3xl mx-auto">
          <div className="text-center mb-10">
            <h2 className="text-2xl sm:text-3xl font-display font-bold text-ink mb-2">
              Frequently Asked Questions
            </h2>
            <p className="text-sm text-ink-dim">
              Everything you need to know about Clip Studio
            </p>
          </div>

          <div className="space-y-3">
            {[
              {
                q: 'How does the AI Virality Score work?',
                a: 'Scores are editorial signals based on the processing pipeline. They are not a guarantee of reach, watch time, or platform approval.'
              },
              {
                q: 'How does active speaker detection work in 9:16 vertical crop?',
                a: 'When the production pipeline is enabled, computer vision tracks facial landmarks and lips in real time. If one person speaks, they remain centered. If two podcast hosts debate, Clip Studio generates a clean split-screen layout.'
              },
              {
                q: 'What video formats and platforms are supported?',
                a: 'The studio accepts approved video platform links and direct MP4, MOV, MKV, WEBM, AVI, MP3, WAV, or M4A uploads. Available render formats depend on the enabled server pipeline.'
              },
              {
                q: 'Is it completely free to try?',
                a: 'Account access does not require a card. Usage limits and processing availability are shown in the product rather than promised as a fixed allowance.'
              }
            ].map((faq, i) => (
              <div
                key={i}
                className="rounded-xl bg-white border border-line overflow-hidden transition-colors shadow-xs"
              >
                <button
                  onClick={() => setOpenFaq(openFaq === i ? null : i)}
                  className="w-full p-4 text-left flex items-center justify-between gap-4 font-display font-semibold text-sm sm:text-base text-ink hover:text-blue-700 transition-colors"
                >
                  <span>{faq.q}</span>
                  <ChevronDown
                    className={`w-4 h-4 text-ink-dim transition-transform duration-200 ${
                      openFaq === i ? 'rotate-180 text-[#0066ff]' : ''
                    }`}
                  />
                </button>
                {openFaq === i && (
                  <div className="p-4 pt-0 text-sm text-ink-muted leading-relaxed border-t border-line-muted">
                    {faq.a}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Clip Preview Modal */}
      {selectedClip && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-md animate-fadeIn">
          <div className="relative w-full max-w-lg rounded-2xl bg-white border border-line p-6 shadow-dropdown">
            <div className="flex items-center justify-between pb-4 border-b border-line mb-4">
              <div>
                <h3 className="text-base font-bold text-ink line-clamp-1">{selectedClip.title}</h3>
                <span className="text-xs font-mono text-blue-700 font-semibold">
                  Virality Index: {selectedClip.score || 95}/100
                </span>
              </div>
              <button
                onClick={() => setSelectedClip(null)}
                className="w-8 h-8 rounded-full bg-subtle flex items-center justify-center text-ink-muted hover:text-ink transition-colors"
              >
                ✕
              </button>
            </div>

            <div className="w-full aspect-[9/16] max-h-[380px] rounded-xl overflow-hidden bg-black relative mb-4 flex items-center justify-center">
              <video
                src={selectedClip.videoUrl}
                controls
                autoPlay
                className="w-full h-full object-cover"
              />
            </div>

            <div className="flex items-center justify-between gap-3 pt-2">
              <span className="text-xs text-ink-dim font-mono">Render settings are shown when processing is enabled</span>
              <a
                href={selectedClip.videoUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="btn-primary text-xs py-2 px-5 font-semibold"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download MP4</span>
              </a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
