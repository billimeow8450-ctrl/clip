import React, { useState } from 'react';
import { User, Mail, Lock, Scissors, AlertCircle, ArrowRight, X, KeyRound, CheckCircle2 } from 'lucide-react';
import { api } from '../api';

export default function AuthPage({ onAuthSuccess, onClose }) {
  // 'login' | 'register' | 'forgot'
  const [authMode, setAuthMode] = useState('login');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  
  // Forgot / Reset password state
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [resetStep, setResetStep] = useState(1); // 1: request token, 2: set new password
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError('');
    setSuccessMessage('');
    setLoading(true);

    try {
      if (authMode === 'login') {
        const res = await api.auth.login({ email_or_username: email, password });
        onAuthSuccess(res.user);
      } else if (authMode === 'register') {
        if (!username) throw new Error('Please enter a username');
        const res = await api.auth.register({ email, username, password });
        onAuthSuccess(res.user);
      } else if (authMode === 'forgot') {
        if (resetStep === 1) {
          const res = await api.auth.forgotPassword(email);
          setSuccessMessage(res.message);
          if (res.reset_token) {
            setResetToken(res.reset_token);
            setResetStep(2);
          }
        } else {
          if (!newPassword || newPassword.length < 6) {
            throw new Error('New password must be at least 6 characters long.');
          }
          const res = await api.auth.resetPassword({ token: resetToken, new_password: newPassword });
          setSuccessMessage(res.message);
          // Return to login after successful password update
          setTimeout(() => {
            setAuthMode('login');
            setResetStep(1);
            setSuccessMessage('Password updated! Please sign in with your new password.');
          }, 1500);
        }
      }
    } catch (err) {
      setError(err.message || 'Authentication operation failed.');
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
            {authMode === 'forgot' ? <KeyRound className="w-6 h-6" /> : <Scissors className="w-6 h-6" />}
          </div>
          <h2 className="text-2xl font-display font-extrabold text-ink">
            {authMode === 'login' && 'Welcome Back'}
            {authMode === 'register' && 'Claim Free Account'}
            {authMode === 'forgot' && (resetStep === 1 ? 'Reset Your Password' : 'Set New Password')}
          </h2>
          <p className="text-xs sm:text-sm text-ink-muted mt-1">
            {authMode === 'login' && 'Sign in to access your viral clips library'}
            {authMode === 'register' && 'Get 75 Free Processing Minutes in 1 click'}
            {authMode === 'forgot' && (resetStep === 1 ? 'Enter your email to generate a reset token' : 'Choose a strong new password for your account')}
          </p>
        </div>

        {authMode !== 'forgot' && (
          <div className="bg-subtle p-1 rounded-xl border border-line flex gap-1 mb-6">
            <button
              type="button"
              onClick={() => { setAuthMode('login'); setError(''); setSuccessMessage(''); }}
              className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                authMode === 'login' ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              onClick={() => { setAuthMode('register'); setError(''); setSuccessMessage(''); }}
              className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                authMode === 'register' ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
              }`}
            >
              Create Account
            </button>
          </div>
        )}

        {error && (
          <div className="p-3 mb-4 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
            <span>{error}</span>
          </div>
        )}

        {successMessage && (
          <div className="p-3 mb-4 bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs rounded-xl flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-600" />
            <span>{successMessage}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 text-left">
          {authMode !== 'forgot' && (
            <>
              <div>
                <label className="block text-xs font-semibold text-ink-muted mb-1.5">
                  {authMode === 'login' ? 'Email or Username' : 'Email Address'}
                </label>
                <div className="relative">
                  <Mail className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    type={authMode === 'login' ? 'text' : 'email'}
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder={authMode === 'login' ? 'user@domain.com or username' : 'name@domain.com'}
                    className="input-field pl-10"
                  />
                </div>
              </div>

              {authMode === 'register' && (
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
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-xs font-semibold text-ink-muted">Password</label>
                  {authMode === 'login' && (
                    <button
                      type="button"
                      onClick={() => { setAuthMode('forgot'); setResetStep(1); setError(''); setSuccessMessage(''); }}
                      className="text-xs font-semibold text-teal-600 hover:text-teal-700 transition-colors"
                    >
                      Forgot password?
                    </button>
                  )}
                </div>
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
            </>
          )}

          {authMode === 'forgot' && resetStep === 1 && (
            <div>
              <label className="block text-xs font-semibold text-ink-muted mb-1.5">Registered Email Address</label>
              <div className="relative">
                <Mail className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@domain.com"
                  className="input-field pl-10"
                />
              </div>
            </div>
          )}

          {authMode === 'forgot' && resetStep === 2 && (
            <>
              <div>
                <label className="block text-xs font-semibold text-ink-muted mb-1.5">Reset Token</label>
                <div className="relative">
                  <KeyRound className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    required
                    value={resetToken}
                    onChange={(e) => setResetToken(e.target.value)}
                    placeholder="Paste your reset token"
                    className="input-field pl-10 font-mono text-xs"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-ink-muted mb-1.5">New Password</label>
                <div className="relative">
                  <Lock className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    type="password"
                    required
                    minLength={6}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="•••••••• (min 6 chars)"
                    className="input-field pl-10"
                  />
                </div>
              </div>
            </>
          )}

          <button
            type="submit"
            disabled={loading}
            className="btn-primary w-full py-3.5 mt-2 text-sm font-bold shadow-button"
          >
            {loading ? (
              <span>Processing...</span>
            ) : (
              <>
                {authMode === 'login' && <span>Sign In to Workspace</span>}
                {authMode === 'register' && <span>Claim 75 Free Minutes</span>}
                {authMode === 'forgot' && resetStep === 1 && <span>Generate Reset Token</span>}
                {authMode === 'forgot' && resetStep === 2 && <span>Save New Password</span>}
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>

          {authMode === 'forgot' && (
            <div className="text-center pt-2">
              <button
                type="button"
                onClick={() => { setAuthMode('login'); setResetStep(1); setError(''); setSuccessMessage(''); }}
                className="text-xs font-semibold text-ink-muted hover:text-teal-600 transition-colors"
              >
                ← Back to Sign In
              </button>
            </div>
          )}
        </form>
      </div>
    </div>
  );
}
