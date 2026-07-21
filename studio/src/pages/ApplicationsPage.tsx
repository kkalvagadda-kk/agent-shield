import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Boxes, Plus, Trash2, KeyRound, Copy, Check, Loader2, X, Power } from 'lucide-react';
import { toast } from 'sonner';
import { useAuth } from '../contexts/AuthContext';
import {
  listApplications,
  createApplication,
  rotateApplicationSecret,
  setApplicationEnabled,
  deleteApplication,
  type Application,
} from '../api/registryApi';

// Applications (Decision 30) — a team-owned, reusable webhook-sending identity.
// Created here once per team, then granted the `invoker` role on specific
// agents/workflows via InvokeAccessPanel. The secret is shown EXACTLY ONCE (on
// create or rotate) — the read model (ApplicationResponse) has no secret field,
// so it is structurally unrecoverable after this page unmounts. Modeled on
// CredentialsPage (team-scoped, contributor-writable, reveal-once); the real
// authorization gate is server-side `can_create_application`, not a client hide.

function errDetail(e: unknown): string | undefined {
  const d = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof d === 'string' ? d : undefined;
}

type Revealed = { name: string; secret: string; kind: 'created' | 'rotated' };

export default function ApplicationsPage() {
  const { team } = useAuth();
  const qc = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState('');
  const [revealed, setRevealed] = useState<Revealed | null>(null);

  const { data: apps = [], isLoading, error } = useQuery({
    queryKey: ['applications', team],
    queryFn: () => listApplications(team as string),
    enabled: !!team,
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ['applications', team] });

  const create = useMutation({
    mutationFn: () => createApplication(team as string, { name: name.trim() }),
    onSuccess: (res) => {
      setRevealed({ name: res.name, secret: res.secret, kind: 'created' });
      setName('');
      setShowCreate(false);
      toast.success('Application created — copy the secret now.');
      invalidate();
    },
    onError: (e) => toast.error(errDetail(e) ?? 'Failed to create application'),
  });

  const rotate = useMutation({
    mutationFn: (app: Application) => rotateApplicationSecret(team as string, app.id),
    onSuccess: (res, app) => {
      setRevealed({ name: app.name, secret: res.secret, kind: 'rotated' });
      toast.success('Secret rotated — copy the new secret now.');
      invalidate();
    },
    onError: () => toast.error('Failed to rotate secret'),
  });

  const toggle = useMutation({
    mutationFn: (app: Application) => setApplicationEnabled(team as string, app.id, !app.enabled),
    onSuccess: (_res, app) => {
      toast.success(app.enabled ? 'Application disabled' : 'Application enabled');
      invalidate();
    },
    onError: () => toast.error('Failed to update application'),
  });

  const remove = useMutation({
    mutationFn: (app: Application) => deleteApplication(team as string, app.id),
    onSuccess: () => {
      toast.success('Application deleted');
      invalidate();
    },
    onError: (e) => toast.error(errDetail(e) ?? 'Failed to delete application'),
  });

  if (!team) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-8">
        <h1 className="text-2xl font-bold text-slate-900">Applications</h1>
        <div className="card flex flex-col items-center py-16 text-center mt-6">
          <Boxes size={40} className="text-slate-300 mb-3" />
          <p className="text-slate-500 font-medium">
            You are not assigned to a team yet.
          </p>
          <p className="text-sm text-slate-400 mt-1">
            Applications are team-scoped — ask an admin to add you to a team.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Applications</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Reusable webhook-sending identities for team <span className="font-medium">{team}</span>.
            Grant one <code className="text-xs">invoker</code> access on an agent or workflow to let it
            send signed webhooks.
          </p>
        </div>
        <button onClick={() => setShowCreate((v) => !v)} className="btn-primary">
          <Plus size={14} /> New Application
        </button>
      </div>

      {/* Reveal-once secret — the only moment this value exists in the UI. */}
      {revealed && (
        <div
          className="card mb-6 p-5 border border-emerald-200 bg-emerald-50"
          data-testid="application-secret-reveal"
        >
          <div className="flex items-center justify-between mb-2">
            <h2 className="text-sm font-semibold text-emerald-800">
              {revealed.kind === 'created' ? 'Application' : 'Secret rotated —'}{' '}
              <span className="font-mono">{revealed.name}</span>
            </h2>
            <button
              onClick={() => setRevealed(null)}
              className="p-1 hover:bg-emerald-100 rounded"
              aria-label="Dismiss secret"
            >
              <X size={16} className="text-emerald-700" />
            </button>
          </div>
          <p className="text-sm font-medium text-emerald-800 mb-2">
            Copy this signing secret now — it won&apos;t be shown again.
          </p>
          <SecretRow secret={revealed.secret} />
          <p className="text-xs text-emerald-700 mt-2">
            It is stored encrypted for the gateway and is never retrievable. If you lose it, rotate to
            mint a new one.
          </p>
        </div>
      )}

      {showCreate && (
        <div className="card mb-6 p-5">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-semibold text-slate-800">New Application</h2>
            <button onClick={() => setShowCreate(false)} className="p-1 hover:bg-slate-100 rounded">
              <X size={16} className="text-slate-400" />
            </button>
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
            className="space-y-4"
          >
            <div>
              <label className="label">Name</label>
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="input"
                placeholder="e.g. billing-service"
                aria-label="Application name"
                autoFocus
              />
              <p className="text-xs text-slate-400 mt-1">
                Names the sending application in webhook signatures and audit logs (1–128 chars).
              </p>
            </div>
            <div className="flex justify-end gap-2">
              <button type="button" onClick={() => setShowCreate(false)} className="btn-secondary">
                Cancel
              </button>
              <button type="submit" disabled={create.isPending || !name.trim()} className="btn-primary">
                {create.isPending && <Loader2 size={14} className="animate-spin mr-1" />}
                Create
              </button>
            </div>
          </form>
        </div>
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load applications: {String(error)}
        </div>
      )}

      {!isLoading && !error && (
        apps.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <Boxes size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No applications yet.</p>
            <p className="text-sm text-slate-400 mt-1">
              Create one, then grant it invoke access on an agent or workflow.
            </p>
            <button onClick={() => setShowCreate(true)} className="btn-primary mt-5">
              <Plus size={14} /> New Application
            </button>
          </div>
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase text-slate-400">
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Status</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {apps.map((a) => (
                  <tr key={a.id} data-testid={`application-row-${a.name}`} className="border-b last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-800 font-mono">{a.name}</td>
                    <td className="px-4 py-3">
                      <span
                        className={`badge text-xs ${a.enabled ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-200 text-slate-600'}`}
                      >
                        {a.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500">
                      {new Date(a.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3 text-right space-x-1">
                      <button
                        onClick={() => rotate.mutate(a)}
                        disabled={rotate.isPending}
                        className="p-1.5 hover:bg-slate-100 rounded"
                        title="Rotate secret"
                        aria-label={`Rotate secret for ${a.name}`}
                      >
                        <KeyRound size={14} className="text-slate-400" />
                      </button>
                      <button
                        onClick={() => toggle.mutate(a)}
                        disabled={toggle.isPending}
                        className="p-1.5 hover:bg-slate-100 rounded"
                        title={a.enabled ? 'Disable' : 'Enable'}
                        aria-label={`${a.enabled ? 'Disable' : 'Enable'} ${a.name}`}
                      >
                        <Power size={14} className={a.enabled ? 'text-emerald-500' : 'text-slate-400'} />
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Delete application "${a.name}"? Any invoker grants it holds are revoked too.`))
                            remove.mutate(a);
                        }}
                        disabled={remove.isPending}
                        className="p-1.5 hover:bg-red-50 rounded"
                        title="Delete"
                        aria-label={`Delete ${a.name}`}
                      >
                        <Trash2 size={14} className="text-red-400" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

function SecretRow({ secret }: { secret: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center gap-2">
      <code
        data-testid="application-secret"
        className="flex-1 text-xs bg-white border border-emerald-200 rounded px-2 py-1.5 break-all"
      >
        {secret}
      </code>
      <button
        onClick={() => {
          navigator.clipboard.writeText(secret);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        }}
        className="btn-secondary text-xs py-1.5"
        aria-label="Copy secret"
      >
        {copied ? <Check size={12} /> : <Copy size={12} />}
      </button>
    </div>
  );
}
