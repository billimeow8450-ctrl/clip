import React, { useEffect, useRef, useState } from 'react';
import { User, Mail, Lock, Scissors, AlertCircle, ArrowRight, X, KeyRound, CheckCircle2, MailCheck } from 'lucide-react';
import { api } from '../api';

export default function AuthPage({ onAuthSuccess, onClose, initialError = '' }) {
  // 'login' | 'register' | 'forgot'
  const [authMode, setAuthMode] = useState('login');
  const [email, setEmail] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  // Forgot / Reset password state
  // Step 1: request token (sent by email) → Step 2: paste token + set new password
  const [resetStep, setResetStep] = useState(1);
  const [resetToken, setResetToken] = useState('');

  // Deep link from the reset email: /?reset_token=<token>#/ opens the reset
  // form with the token prefilled (the SPA uses hash routing, so the token
  // rides in the query string before the # fragment).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get('reset_token');
    if (token) {
      setAuthMode('forgot');
      setResetStep(2);
      setResetToken(token);
      params.delete('reset_token');
      const rest = params.toString();
      window.history.replaceState(
        {},
        '',
        `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`,
      );
    }
  }, []);

  const [newPassword, setNewPassword] = useState('');

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [successMessage, setSuccessMessage] = useState('');
  const [googleAvailable, setGoogleAvailable] = useState(false);

  const dialogRef = useRef(null);

  // Modal semantics: Escape to close, focus the dialog on open (finding U5)
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') onClose?.();
    };
    window.addEventListener('keydown', onKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  useEffect(() => {
    setError(initialError);
  }, [initialError]);

  useEffect(() => {
    api.auth.getProviders().then((providers) => setGoogleAvailable(Boolean(providers.google))).catch(() => setGoogleAvailable(false));
  }, []);

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
          // The backend no longer returns the token (finding C1): it is sent
          // by email. A generic message is shown for unknown addresses too.
          setSuccessMessage(res.message || 'Check your inbox for the reset link.');
          setResetStep(2);
        } else {
          if (!newPassword || newPassword.length < 8) {
            throw new Error('New password must be at least 8 characters.');
          }
          const res = await api.auth.resetPassword({ token: resetToken.trim(), new_password: newPassword });
          setSuccessMessage(res.message);
          setTimeout(() => {
            setAuthMode('login');
            setResetStep(1);
            setResetToken('');
            setNewPassword('');
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

  const switchMode = (mode) => {
    setAuthMode(mode);
    setError('');
    setSuccessMessage('');
    setResetStep(1);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-3 sm:p-6 bg-slate-900/50 backdrop-blur-md animate-fadeIn">
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={authMode === 'login' ? 'Sign in' : authMode === 'register' ? 'Create account' : 'Reset password'}
        tabIndex={-1}
        className="relative my-auto max-h-[calc(100dvh-1.5rem)] w-full max-w-md overflow-y-auto overscroll-contain p-5 sm:p-8 rounded-2xl bg-white border border-line shadow-dropdown outline-none"
      >
        {onClose && (
          <button
            onClick={onClose}
            aria-label="Close"
            className="absolute top-3 right-3 sm:top-5 sm:right-5 min-w-11 min-h-11 rounded-full bg-subtle flex items-center justify-center text-ink-muted hover:text-ink focus-visible:ring-2 focus-visible:ring-blue-500 transition-colors"
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
            {authMode === 'forgot' && (resetStep === 1 ? 'Reset Your Password' : 'Check Your Email')}
          </h2>
          <p className="text-xs sm:text-sm text-ink-muted mt-1">
            {authMode === 'login' && 'Sign in to access your viral clips library'}
            {authMode === 'register' && 'Create your free account in under a minute'}
            {authMode === 'forgot' && (resetStep === 1
              ? 'Enter your email and we will send you a reset link'
              : 'Paste the reset token from your email and choose a new password')}
          </p>
        </div>

        {authMode !== 'forgot' && (
          <div className="bg-subtle p-1 rounded-xl border border-line flex gap-1 mb-6">
            <button
              type="button"
              onClick={() => switchMode('login')}
              aria-pressed={authMode === 'login'}
              className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                authMode === 'login' ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
              }`}
            >
              Sign In
            </button>
            <button
              type="button"
              onClick={() => switchMode('register')}
              aria-pressed={authMode === 'register'}
              className={`flex-1 py-2 rounded-lg text-xs sm:text-sm font-bold transition-all ${
                authMode === 'register' ? 'bg-white text-teal-700 shadow-xs border border-line' : 'text-ink-muted hover:text-ink'
              }`}
            >
              Create Account
            </button>
          </div>
        )}

        {error && (
          <div className="p-3 mb-4 bg-rose-50 border border-rose-200 text-rose-700 text-xs rounded-xl flex items-center gap-2" role="alert">
            <AlertCircle className="w-4 h-4 shrink-0 text-rose-600" />
            <span>{error}</span>
          </div>
        )}

        {successMessage && (
          <div className="p-3 mb-4 bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs rounded-xl flex items-center gap-2" role="status">
            {authMode === 'forgot' && resetStep === 2 ? (
              <MailCheck className="w-4 h-4 shrink-0 text-emerald-600" />
            ) : (
              <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-600" />
            )}
            <span>{successMessage}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 text-left">
          {authMode !== 'forgot' && (
            <>
              <div>
                <label htmlFor="auth-email" className="block text-xs font-semibold text-ink-muted mb-1.5">
                  {authMode === 'login' ? 'Email or Username' : 'Email Address'}
                </label>
                <div className="relative">
                  <Mail className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    id="auth-email"
                    type={authMode === 'login' ? 'text' : 'email'}
                    required
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder={authMode === 'login' ? 'user@domain.com or username' : 'name@domain.com'}
                    className="input-field has-leading-icon"
                    autoComplete="username"
                  />
                </div>
              </div>

              {authMode === 'register' && (
                <div>
                  <label htmlFor="auth-username" className="block text-xs font-semibold text-ink-muted mb-1.5">Username</label>
                  <div className="relative">
                    <User className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                    <input
                      id="auth-username"
                      type="text"
                      required
                      minLength={3}
                      maxLength={32}
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="creator101"
                      className="input-field has-leading-icon"
                      autoComplete="username"
                    />
                  </div>
                  <p className="text-[10px] text-ink-dim mt-1">3–32 characters: letters, numbers, dot, dash or underscore.</p>
                </div>
              )}

              <div>
                <div className="flex items-center justify-between mb-1.5">
                  <label htmlFor="auth-password" className="block text-xs font-semibold text-ink-muted">Password</label>
                  {authMode === 'login' && (
                    <button
                      type="button"
                      onClick={() => switchMode('forgot')}
                      className="text-xs font-semibold text-teal-600 hover:text-teal-700 transition-colors"
                    >
                      Forgot password?
                    </button>
                  )}
                </div>
                <div className="relative">
                  <Lock className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    id="auth-password"
                    type="password"
                    required
                    minLength={authMode === 'register' ? 8 : 1}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={authMode === 'register' ? 'min 8 characters' : '••••••••'}
                    className="input-field has-leading-icon"
                    autoComplete={authMode === 'register' ? 'new-password' : 'current-password'}
                  />
                </div>
                {authMode === 'register' && (
                  <p className="text-[10px] text-ink-dim mt-1">At least 8 characters with a letter and a number.</p>
                )}
              </div>
            </>
          )}

          {authMode === 'forgot' && resetStep === 1 && (
            <div>
              <label htmlFor="reset-email" className="block text-xs font-semibold text-ink-muted mb-1.5">Registered Email Address</label>
              <div className="relative">
                <Mail className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                <input
                  id="reset-email"
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@domain.com"
                  className="input-field has-leading-icon"
                  autoComplete="email"
                />
              </div>
            </div>
          )}

          {authMode === 'forgot' && resetStep === 2 && (
            <>
              <div>
                <label htmlFor="reset-token" className="block text-xs font-semibold text-ink-muted mb-1.5">Reset Token</label>
                <div className="relative">
                  <KeyRound className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    id="reset-token"
                    type="text"
                    required
                    value={resetToken}
                    onChange={(e) => setResetToken(e.target.value)}
                    placeholder="Paste the token from your email"
                    className="input-field has-leading-icon font-mono text-xs"
                  />
                </div>
              </div>

              <div>
                <label htmlFor="new-password" className="block text-xs font-semibold text-ink-muted mb-1.5">New Password</label>
                <div className="relative">
                  <Lock className="w-4 h-4 text-ink-dim absolute left-3.5 top-1/2 -translate-y-1/2" />
                  <input
                    id="new-password"
                    type="password"
                    required
                    minLength={8}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="•••••••• (min 8 chars)"
                    className="input-field has-leading-icon"
                    autoComplete="new-password"
                  />
                </div>
                <p className="text-[10px] text-ink-dim mt-1">At least 8 characters with a letter and a number.</p>
              </div>
            </>
          )}

          {authMode !== 'forgot' && (
            <>
              <div className="relative my-5 flex items-center" aria-hidden="true"><div className="grow border-t border-line" /><span className="mx-3 text-[11px] font-medium text-ink-dim">or</span><div className="grow border-t border-line" /></div>
              <button
                type="button"
                onClick={() => { window.location.assign(api.auth.googleStartUrl()); }}
                disabled={!googleAvailable}
                title={googleAvailable ? 'Continue with Google' : 'Google sign-in is being configured'}
                className="btn-secondary w-full min-h-11 text-sm disabled:cursor-not-allowed disabled:opacity-60"
              >
                <svg aria-hidden="true" viewBox="0 0 24 24" className="h-5 w-5" focusable="false"><path fill="#4285F4" d="M21.35 12.2c0-.65-.06-1.27-.17-1.87H12v3.54h5.24a4.48 4.48 0 0 1-1.94 2.94v2.3h3.14c1.84-1.7 2.91-4.2 2.91-6.91Z"/><path fill="#34A853" d="M12 21.75c2.64 0 4.85-.88 6.47-2.39l-3.14-2.3c-.88.59-2  .94-3.33.94-2.56 0-4.73-1.73-5.5-4.05H3.27v2.37A9.76 9.76 0 0 0 12 21.75Z"/><path fill="#FBBC05" d="M6.5 13.95A5.86 5.86 0 0 1 6.2 12c0-.68.12-1.34.3-1.95V7.68H3.27A9.75 9.75 0 0 0 2.25 12c0 1.57.38 3.06 1.02 4.32l3.23-2.37Z"/><path fill="#EA4335" d="M12 6c1.44 0 2.73.5 3.75 1.48l2.81-2.8C16.84 3.08 14.64 2.25 12 2.25a9.76 9.76 0 0 0-8.73 5.43l3.23 2.37C7.27 7.73 9.44 6 12 6Z"/></svg>
                Continue with Google
              </button>
              {!googleAvailable && <p className="mt-2 text-center text-[11px] text-ink-dim">Google sign-in is being configured. Email sign-in is available now.</p>}
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
                {authMode === 'register' && <span>Create Free Account</span>}
                {authMode === 'forgot' && resetStep === 1 && <span>Send Reset Link</span>}
                {authMode === 'forgot' && resetStep === 2 && <span>Save New Password</span>}
                <ArrowRight className="w-4 h-4" />
              </>
            )}
          </button>

          {authMode === 'forgot' && (
            <div className="text-center pt-2">
              <button
                type="button"
                onClick={() => switchMode('login')}
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
