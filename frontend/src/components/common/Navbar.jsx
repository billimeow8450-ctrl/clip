import React from 'react';
import { Scissors, FolderKanban, LogOut, Flame, Sparkles, Zap } from 'lucide-react';

export default function Navbar({ activeTab, setActiveTab, user, onLogout, onOpenAuth }) {
  return (
    <header className="sticky top-0 z-50 bg-white/80 backdrop-blur-xl border-b border-slate-100 transition-all">
      <div className="container-custom h-20 flex items-center justify-between">
        {/* Brand logo lockup matching reference logo style */}
        <div
          onClick={() => setActiveTab('landing')}
          className="flex items-center gap-2.5 cursor-pointer select-none group"
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
        </div>

        {/* Center nav with active indicator */}
        <nav className="hidden md:flex items-center gap-8">
          <button
            onClick={() => setActiveTab('landing')}
            className="relative text-sm font-semibold text-slate-950 transition-colors py-1 cursor-pointer"
          >
            AI Clipper
            <span className="absolute bottom-0 inset-x-0 h-0.5 bg-[#0066ff] rounded-full"></span>
          </button>
          <a
            href="#studio"
            className="text-sm font-medium text-slate-600 hover:text-slate-950 transition-colors"
          >
            Studio Showcase
          </a>
          <a
            href="#features"
            className="text-sm font-medium text-slate-600 hover:text-slate-950 transition-colors"
          >
            Features
          </a>
          <a
            href="#formats"
            className="text-sm font-medium text-slate-600 hover:text-slate-950 transition-colors"
          >
            Viral Formats
          </a>
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
                <span className="w-1.5 h-1.5 rounded-full bg-[#0066ff]"></span>
              </button>
              <button
                onClick={onLogout}
                title="Sign Out"
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
