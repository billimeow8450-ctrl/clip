import React, { useState, useEffect } from 'react';
import { FolderKanban, Scissors, Download, Clock, AlertCircle, RefreshCw, Loader2, Sparkles, TrendingUp } from 'lucide-react';
import { api } from '../api';

export default function ProjectsPage({ user, onRequireAuth }) {
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  const fetchJobs = async () => {
    if (!user) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      const list = await api.jobs.list();
      setJobs(list || []);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
  }, [user]);

  if (!user) {
    return (
      <div className="card p-12 max-w-md mx-auto text-center space-y-4 bg-white border border-line shadow-card">
        <div className="w-12 h-12 rounded-2xl bg-teal-50 border border-teal-200 flex items-center justify-center text-teal-600 mx-auto">
          <FolderKanban className="w-6 h-6" />
        </div>
        <h2 className="text-xl font-display font-bold text-ink">Sign in to access your clips library</h2>
        <p className="text-xs sm:text-sm text-ink-muted">
          Your generated viral clips, virality scores, and exported videos are saved securely in your library.
        </p>
        <button onClick={onRequireAuth} className="btn-primary py-2.5 px-6 text-sm font-bold mx-auto shadow-button">
          Sign In / Create Account
        </button>
      </div>
    );
  }

  const filteredJobs = jobs.filter((job) => {
    if (filter === 'all') return true;
    return job.status === filter;
  });

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-20 text-left">
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div>
          <h1 className="text-2xl font-display font-extrabold text-ink">My Clips & Projects</h1>
          <p className="text-xs sm:text-sm text-ink-muted mt-1">
            Browse and download your generated viral shorts and rendering history.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <div className="bg-subtle p-1 rounded-full border border-line flex gap-1">
            {['all', 'completed', 'processing', 'failed'].map((f) => (
              <button
                key={f}
                type="button"
                onClick={() => setFilter(f)}
                className={`py-1.5 px-3.5 rounded-full text-xs font-semibold capitalize transition-all ${
                  filter === f ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-dim hover:text-ink'
                }`}
              >
                {f}
              </button>
            ))}
          </div>

          <button
            onClick={fetchJobs}
            disabled={loading}
            className="w-8 h-8 rounded-full bg-white border border-line flex items-center justify-center text-ink-dim hover:text-ink transition-colors shadow-xs"
            title="Refresh"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading && jobs.length === 0 ? (
        <div className="text-center py-16">
          <Loader2 className="w-8 h-8 text-teal-600 animate-spin mx-auto mb-2" />
          <span className="text-sm text-ink-dim font-mono">Loading your viral clips...</span>
        </div>
      ) : filteredJobs.length === 0 ? (
        <div className="card p-12 text-center text-ink-muted bg-white border border-line shadow-card">
          <FolderKanban className="w-10 h-10 mx-auto mb-3 text-teal-600" />
          <p className="font-bold text-ink text-sm">No {filter !== 'all' ? filter : ''} clips found.</p>
          <p className="text-xs sm:text-sm mt-1 text-ink-dim">Drop a YouTube link on the homepage to generate your first clip!</p>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredJobs.map((job) => {
            const isCompleted = job.status === 'completed';
            const isProcessing = job.status === 'processing' || job.status === 'queued';
            const isFailed = job.status === 'failed';

            return (
              <div key={job.id} className="card p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-white border border-line hover:border-teal-400 transition-colors shadow-card">
                <div className="flex items-start gap-3.5 min-w-0">
                  <div className="w-10 h-10 rounded-xl bg-teal-50 border border-teal-200 flex items-center justify-center text-teal-600 shrink-0">
                    <Scissors className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="font-mono text-xs text-ink-dim">{job.id}</span>
                      <span className="badge-teal text-[10px] py-0.5 px-2">{job.type}</span>
                    </div>
                    <h4 className="font-bold text-sm text-ink truncate">
                      {job.result_data?.title || `Clip Project ${job.id}`}
                    </h4>
                    <div className="text-xs text-ink-dim flex items-center gap-3 mt-1 font-mono">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {new Date(job.created_at).toLocaleDateString()}
                      </span>
                      {job.stage && (
                        <span className="text-teal-700 truncate">• {job.stage}</span>
                      )}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3 self-end sm:self-center shrink-0">
                  {isProcessing && (
                    <div className="flex items-center gap-2 text-xs font-bold text-teal-700 font-mono">
                      <Loader2 className="w-4 h-4 animate-spin" />
                      <span>{Math.round(job.progress || 0)}%</span>
                    </div>
                  )}

                  {isCompleted && (
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-semibold text-teal-700 bg-teal-50 px-2.5 py-1 rounded-full border border-teal-200 flex items-center gap-1">
                        <TrendingUp className="w-3.5 h-3.5" />
                        Completed
                      </span>
                      {job.result_data?.output_video && (
                        <a
                          href={job.result_data.output_video}
                          download
                          className="btn-primary text-xs py-1.5 px-3.5 shadow-xs"
                        >
                          <Download className="w-3.5 h-3.5" />
                          <span>Download MP4</span>
                        </a>
                      )}
                    </div>
                  )}

                  {isFailed && (
                    <span className="text-xs font-semibold text-rose-700 bg-rose-50 px-2.5 py-1 rounded-full border border-rose-200 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3" />
                      Failed
                    </span>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
