import React from 'react';
import { Scissors, FolderKanban, LogOut, FileText, WandSparkles } from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab, user, onLogout, onOpenAuth }) {
  return (
    <header className="sticky top-0 z-50 bg-white/95 backdrop-blur-xl border-b border-slate-100 transition-all">
      <div className="container-custom min-h-16 py-3 flex items-center justify-between gap-4">
        {/* Brand logo lockup matching reference logo style */}
        <button
          type="button"
          onClick={() => setActiveTab('landing')}
          className="flex items-center gap-2.5 cursor-pointer select-none group shrink-0"
          aria-label="OpusPulse home"
        >
          <div className="w-9 h-9 rounded-xl bg-slate-950 flex items-center justify-center text-white shadow-xs group-hover:scale-105 transition-transform">
            <Scissors className="w-4 h-4 text-blue-400" />
          </div>
          <div className="flex items-center gap-2">
            <span className="font-display font-black text-xl tracking-tight text-slate-950 flex items-center">
              Opus<span className="text-[#0066ff]">Pulse</span>
            </span>
            <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-blue-50 text-blue-700 border border-blue-200 font-bold">
              AI 2.5
            </span>
          </div>
        </button>

        {/* Center nav with active indicator */}
        <nav className="hidden lg:flex items-center gap-1" aria-label="Primary navigation">
          {[
            { id: 'clipper', label: 'AI Clipper', icon: Scissors },
            { id: 'editor', label: 'Video Editor', icon: WandSparkles },
            { id: 'transcriber', label: 'Transcriber', icon: FileText },
            { id: 'projects', label: 'Projects', icon: FolderKanban },
          ].map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              onClick={() => setActiveTab(id)}
              aria-current={activeTab === id ? 'page' : undefined}
              className={`min-h-11 px-3.5 rounded-full text-sm font-semibold transition-colors flex items-center gap-2 ${
                activeTab === id
                  ? 'bg-blue-50 text-blue-700'
                  : 'text-slate-600 hover:text-slate-950 hover:bg-slate-50'
              }`}
            >
              <Icon className="w-4 h-4" aria-hidden="true" />
              {label}
            </button>
          ))}
        </nav>

        {/* Right: Pill Auth & Free Trial Actions */}
        <div className="flex items-center gap-3">
          {user ? (
            <div className="flex items-center gap-3">
              <button
                onClick={() => setActiveTab('projects')}
                className="px-4 py-2 rounded-full border border-slate-200 text-xs sm:text-sm font-semibold text-slate-800 hover:bg-slate-50 transition-colors flex items-center gap-2"
              >
                <span>{user.username}</span>
                <FolderKanban className="w-3.5 h-3.5 text-[#0066ff]" aria-hidden="true" />
              </button>
              <button
                onClick={onLogout}
                title="Sign Out"
                aria-label="Sign out"
                className="w-9 h-9 rounded-full border border-slate-200 flex items-center justify-center text-slate-500 hover:text-rose-500 hover:border-rose-200 transition-colors"
              >
                <LogOut className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={onOpenAuth}
                className="px-5 py-2 rounded-full border border-slate-300 text-xs sm:text-sm font-semibold text-slate-800 hover:bg-slate-50 hover:border-slate-400 transition-all shadow-2xs cursor-pointer"
              >
                Sign in
              </button>
              <button
                onClick={onOpenAuth}
                className="px-5 py-2 rounded-full bg-slate-950 hover:bg-slate-800 text-white text-xs sm:text-sm font-semibold transition-all shadow-sm hover:scale-[1.02] active:scale-[0.98] cursor-pointer"
              >
                Get Free Clips
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
