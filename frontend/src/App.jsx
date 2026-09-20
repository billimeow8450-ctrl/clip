import React, { useState, useEffect } from 'react';
import Navbar from './components/common/Navbar';
import LandingPage from './pages/LandingPage';
import ProjectsPage from './pages/ProjectsPage';
import AuthPage from './pages/AuthPage';
import { api } from './api';
import { Scissors, Zap, Shield, Sparkles } from 'lucide-react';

export default function App() {
  const [activeTab, setActiveTab] = useState('landing');
  const [user, setUser] = useState(null);
  const [showAuthModal, setShowAuthModal] = useState(false);

  useEffect(() => {
    const stored = api.auth.getCurrentUser();
    if (stored) {
      setUser(stored);
    }
  }, []);

  const handleLogout = () => {
    api.auth.logout();
    setUser(null);
    setActiveTab('landing');
  };

  const handleAuthSuccess = (userData) => {
    setUser(userData);
    setShowAuthModal(false);
  };

  return (
    <div className="bg-canvas text-ink-body min-h-screen flex flex-col justify-between selection:bg-teal-500/20 selection:text-teal-900">
      <div>
        <Navbar
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          user={user}
          onLogout={handleLogout}
          onOpenAuth={() => setShowAuthModal(true)}
        />

        <main>
          {activeTab === 'landing' && (
            <LandingPage setActiveTab={setActiveTab} />
          )}

          {activeTab === 'projects' && (
            <div className="container-custom pt-10">
              <ProjectsPage
                user={user}
                onRequireAuth={() => setShowAuthModal(true)}
              />
            </div>
          )}
        </main>
      </div>

      {showAuthModal && (
        <AuthPage
          onAuthSuccess={handleAuthSuccess}
          onClose={() => setShowAuthModal(false)}
        />
      )}

      {/* Shared Footer matching Clean Light Theme */}
      <footer className="bg-white border-t border-line py-16 mt-20 text-xs text-ink-dim shadow-xs">
        <div className="container-custom">
          <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-8 pb-10 border-b border-line">
            <div className="flex flex-col gap-3">
              <div className="flex items-center gap-3">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-teal-600 to-teal-500 flex items-center justify-center text-white shadow-xs">
                  <Scissors className="w-4 h-4" />
                </div>
                <span className="font-display font-bold text-lg text-ink">
                  Opus<span className="text-teal-600">Pulse</span> AI
                </span>
                <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-teal-50 text-teal-700 border border-teal-200 uppercase font-semibold">
                  PRO 2.5
                </span>
              </div>
              <p className="text-sm text-ink-muted max-w-sm leading-relaxed">
                Autonomous video intelligence designed to turn long podcasts & videos into high-growth short-form audience engines.
              </p>
            </div>

            <div className="flex flex-wrap items-center gap-6 text-sm font-medium">
              <a href="#features" className="hover:text-ink transition-colors">Features</a>
              <a href="#studio" className="hover:text-ink transition-colors">AI Studio</a>
              <a href="#virality" className="hover:text-ink transition-colors">Virality Score</a>
              <a href="#faq" className="hover:text-ink transition-colors">FAQ</a>
              <span className="text-ink-dim hover:text-ink cursor-pointer">API Docs</span>
              <span className="text-ink-dim hover:text-ink cursor-pointer">Affiliates</span>
            </div>
          </div>

          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 pt-8 text-ink-dim">
            <p>© {new Date().getFullYear()} OpusPulse AI Inc. Computational short-form video intelligence. All rights reserved.</p>
            <div className="flex items-center gap-4">
              <span className="inline-flex items-center gap-2 text-teal-700 font-semibold">
                <span className="w-2 h-2 rounded-full bg-teal-500 pulse-teal-dot"></span>
                All AI Systems Operational (99.98%)
              </span>
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
