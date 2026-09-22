import React, { useEffect, useState } from 'react';
import { Activity, Database, FolderKanban, RefreshCw, ShieldCheck, Users } from 'lucide-react';
import { api } from '../api';

const metricConfig = [
  ['users', 'Accounts', Users],
  ['projects', 'Projects', FolderKanban],
  ['jobs', 'Jobs', Activity],
  ['files', 'Uploads', Database],
];

export default function AdminPage({ user }) {
  const [overview, setOverview] = useState(null);
  const [users, setUsers] = useState([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    setError('');
    try {
      const [nextOverview, nextUsers] = await Promise.all([
        api.admin.overview(),
        api.admin.users(),
      ]);
      setOverview(nextOverview);
      setUsers(nextUsers);
    } catch (err) {
      setError(err.message || 'Could not load operations data.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { refresh(); }, []);

  if (!user?.is_admin) {
    return <div className="card max-w-xl mx-auto p-10 text-center"><ShieldCheck className="w-9 h-9 mx-auto mb-3 text-slate-400" /><h1 className="text-xl font-bold">Admin access required</h1></div>;
  }

  return (
    <section className="max-w-6xl mx-auto pb-20 space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <p className="text-xs uppercase tracking-[0.2em] font-bold text-blue-600">Clip Studio</p>
          <h1 className="text-3xl font-black mt-2">Operations console</h1>
          <p className="text-sm text-ink-muted mt-2">Live service activity and account support, protected by your server-side admin allowlist.</p>
        </div>
        <button onClick={refresh} disabled={loading} className="btn-secondary"><RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} /> Refresh</button>
      </div>

      {error && <p className="rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700" role="alert">{error}</p>}

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {metricConfig.map(([key, label, Icon]) => (
          <article className="card p-5" key={key}>
            <Icon className="w-5 h-5 text-blue-600 mb-7" />
            <p className="text-3xl font-black tabular-nums">{overview?.[key] ?? '—'}</p>
            <p className="text-xs text-ink-muted mt-1">{label}</p>
          </article>
        ))}
      </div>

      <article className="card overflow-hidden">
        <div className="p-5 border-b border-line flex items-center justify-between">
          <div><h2 className="font-bold">Recent accounts</h2><p className="text-xs text-ink-muted mt-1">No credentials or password data are ever shown here.</p></div>
          <span className="text-xs text-ink-dim">{overview?.active_jobs ?? 0} active jobs</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-subtle text-xs uppercase tracking-wide text-ink-muted"><tr><th className="p-4">User</th><th className="p-4">Plan</th><th className="p-4">Joined</th></tr></thead>
            <tbody>{users.map((account) => <tr className="border-t border-line" key={account.id}><td className="p-4"><p className="font-semibold">{account.username}</p><p className="text-xs text-ink-muted">{account.email}</p></td><td className="p-4 capitalize">{account.tier}</td><td className="p-4 text-ink-muted">{new Date(account.created_at).toLocaleDateString()}</td></tr>)}</tbody>
          </table>
        </div>
      </article>
    </section>
  );
}
