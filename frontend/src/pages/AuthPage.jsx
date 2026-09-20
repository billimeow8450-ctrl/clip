import React, { useState } from 'react';
import { User, Mail, Lock, Scissors, AlertCircle, ArrowRight, X, Sparkles } from 'lucide-react';
import { api } from '../api';

export default function AuthPage({ onAuthSuccess, onClose }) {
  const [isLogin, setIsLogin] = useState(true);
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      let res;
      if (isLogin) {
        res = await api.auth.login({ email_or_username: email, password });
      } else {
        if (!username) throw new Error('Please enter a username');
        res = await api.auth.register({ email, username, password });
      }
      onAuthSuccess(res.user);
    } catch (err) {
      setError(err.message || 'Authentication failed. Please verify credentials.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/50 backdrop-blur-md animate-fadeIn">
      <div className="relative max-w-md w-full p-8 rounded-2xl bg-white border border-line shadow-dropdown">
        {onClose && (
          <button
            onClick={onClose}
            className="absolute top-5 right-5 w-8 h-8 rounded-full bg-subtle flex items-center justify-center text-ink-muted hover:text-ink transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        )}

        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-teal-600 to-teal-500 mx-auto flex items-center justify-center text-white mb-3 shadow-md shadow-teal-500/25">
            <Scissors className="w-6 h-6" />
          </div>
          <h2 className="text-2xl font-display font-extrabold text-ink">
            {isLogin ? 'Welcome Back' : 'Claim Free Account'}
          </h2>
          <p className="text-xs sm:text-sm text-ink-muted mt-1">
            {isLogin ? 'Sign in to access your viral clips library' : 'Get 75 Free Processing Minutes in 1 click'}
          </p>
        </div>

        <div className="bg-subtle p-1 rounded-xl border border-line flex gap-1 mb-6">
          <button
            type="button"
            onClick={() => { setIsLogin(true); setError(''); }}
            className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
              isLogin ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
            }`}
          >
            Sign In
          </button>
          <button
            type="button"
            onClick={() => { setIsLogin(false); setError(''); }}
            className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
              !isLogin ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
            }`}
          >
            Create Account
          </button>
        </div>

        {error && (
          <div className="p-3 mb-4 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
            <span>{error}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 text-left">
          <div>
            <label className="block text-xs font-semibold text-ink-muted mb-1.5">
              {isLogin ? 'Email or Username' : 'Email Address'}
            </label>
            <div className="relative">
              <Mail className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type={isLogin ? 'text' : 'email'}
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder={isLogin ? 'user@domain.com or username' : 'name@domain.com'}
                className="input-field pl-10"
              />
            </div>
          </div>

          {!isLogin && (
            <div>
              <label className="block text-xs font-semibold text-ink-muted mb-1.5">Username</label>
              <div className="relative">
                <User className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="creator101"
                  className="input-field pl-10"
                />
              </div>
            </div>
          )}

          <div>
            <label className="block text-xs font-semibold text-ink-muted mb-1.5">Password</label>
            <div className="relative">
              <Lock className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="input-field pl-10"
              />
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-3.5 mt-2 text-sm font-bold shadow-button"
          >
            {loading ? (
              <span>Authenticating...</span>
            ) : (
              <>
                <span>{isLogin ? 'Sign In to Workspace' : 'Claim 75 Free Minutes'}</span>
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
