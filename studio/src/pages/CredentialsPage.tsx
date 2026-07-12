import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { zodResolver } from '@hookform/resolvers/zod';
import { KeyRound, Loader2, Pencil, Plus, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';
import {
  createAuthConfig,
  deleteAuthConfig,
  listAuthConfigs,
  updateAuthConfig,
  type AuthConfig,
  type CreateAuthConfigPayload,
} from '../api/registryApi';

const schema = z.object({
  name: z.string().min(1, 'Name is required'),
  type: z.enum(['api_key', 'oauth2', 'bearer', 'mtls']),
  credential_key: z.string().optional(),
  credential_value: z.string().optional(),
  owner_team: z.string().optional(),
});

type FormValues = z.infer<typeof schema>;

// Fingerprints of HTTP/httpx error strings that must never be saved as a secret
// value (e.g. a pasted "Client error '403 Forbidden' for url '...'"). Guards the
// save handler so a wrong clipboard paste is caught before it reaches the API.
const HTTP_ERROR_FINGERPRINT =
  /(Client error '|Server error '|' for url '|Traceback \(most recent call last\)|\bForbidden\b|\bUnauthorized\b)/;

function assertLooksLikeCredential(value: string): void {
  if (HTTP_ERROR_FINGERPRINT.test(value)) {
    throw new Error(
      'That value looks like an HTTP error message, not a secret. Paste the actual API key or token.',
    );
  }
  if (value.length > 1024) {
    throw new Error('Secret value is too long to be a valid API key or token.');
  }
}

const TYPE_LABELS: Record<string, string> = {
  api_key: 'API Key',
  oauth2: 'OAuth2',
  bearer: 'Bearer Token',
  mtls: 'mTLS',
};

const TYPE_BADGE: Record<string, string> = {
  api_key: 'bg-blue-100 text-blue-700',
  oauth2: 'bg-purple-100 text-purple-700',
  bearer: 'bg-green-100 text-green-700',
  mtls: 'bg-amber-100 text-amber-700',
};

export default function CredentialsPage() {
  const qc = useQueryClient();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingConfig, setEditingConfig] = useState<AuthConfig | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['auth-configs'],
    queryFn: () => listAuthConfigs(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteAuthConfig(id),
    onSuccess: () => {
      toast.success('Credential deleted.');
      qc.invalidateQueries({ queryKey: ['auth-configs'] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : (detail as { message?: string })?.message;
      toast.error(msg ?? 'Failed to delete. It may be linked to tools.');
    },
  });

  const configs: AuthConfig[] = data?.items ?? [];

  const openCreate = () => { setEditingConfig(null); setShowCreateForm(true); };
  const openEdit = (c: AuthConfig) => { setShowCreateForm(false); setEditingConfig(c); };
  const closeForm = () => { setShowCreateForm(false); setEditingConfig(null); };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Credentials</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Manage API keys and tokens for tools that call external services
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary">
          <Plus size={14} /> New Credential
        </button>
      </div>

      {(showCreateForm || editingConfig) && (
        <CredentialForm
          config={editingConfig}
          onClose={closeForm}
          onSaved={() => {
            closeForm();
            qc.invalidateQueries({ queryKey: ['auth-configs'] });
          }}
        />
      )}

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load credentials: {String(error)}
        </div>
      )}

      {!isLoading && !error && (
        configs.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <KeyRound size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No credentials yet.</p>
            <button onClick={openCreate} className="btn-primary mt-5">
              <Plus size={14} /> New Credential
            </button>
          </div>
        ) : (
          <div className="card overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b text-left text-xs uppercase text-slate-400">
                  <th className="px-4 py-3">Name</th>
                  <th className="px-4 py-3">Type</th>
                  <th className="px-4 py-3">Team</th>
                  <th className="px-4 py-3">Created</th>
                  <th className="px-4 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {configs.map((c) => (
                  <tr key={c.id} className="border-b last:border-0 hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-800">{c.name}</td>
                    <td className="px-4 py-3">
                      <span className={`badge text-xs ${TYPE_BADGE[c.type] ?? 'bg-slate-100 text-slate-600'}`}>
                        {TYPE_LABELS[c.type] ?? c.type}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-500">{c.owner_team ?? '—'}</td>
                    <td className="px-4 py-3 text-slate-500">
                      {new Date(c.created_at).toLocaleDateString()}
                    </td>
                    <td className="px-4 py-3 text-right space-x-1">
                      <button onClick={() => openEdit(c)} className="p-1.5 hover:bg-slate-100 rounded" title="Edit">
                        <Pencil size={14} className="text-slate-400" />
                      </button>
                      <button
                        onClick={() => {
                          if (confirm(`Delete credential "${c.name}"?`)) deleteMutation.mutate(c.id);
                        }}
                        className="p-1.5 hover:bg-red-50 rounded"
                        title="Delete"
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

function CredentialForm({
  config,
  onClose,
  onSaved,
}: {
  config: AuthConfig | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = config !== null;

  const { register, handleSubmit, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      name: config?.name ?? '',
      type: (config?.type as FormValues['type']) ?? 'api_key',
      credential_key: '',
      credential_value: '',
      owner_team: config?.owner_team ?? '',
    },
  });

  const mutation = useMutation({
    mutationFn: async (values: FormValues) => {
      const payload: CreateAuthConfigPayload = {
        name: values.name,
        type: values.type,
        owner_team: values.owner_team || undefined,
      };
      if (values.credential_key && values.credential_value) {
        assertLooksLikeCredential(values.credential_value);
        payload.credentials = { [values.credential_key]: values.credential_value };
      }
      if (isEdit) {
        return updateAuthConfig(config!.id, payload);
      }
      return createAuthConfig(payload);
    },
    onSuccess: () => {
      toast.success(isEdit ? 'Credential updated.' : 'Credential created.');
      onSaved();
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      let msg: string | undefined;
      if (typeof detail === 'string') {
        msg = detail;
      } else if (Array.isArray(detail)) {
        // Pydantic 422 validation errors arrive as a list of {msg, loc, ...}.
        msg = (detail[0] as { msg?: string })?.msg;
      }
      // Fall back to a client-side guard Error (assertLooksLikeCredential).
      toast.error(msg ?? (err as Error)?.message ?? 'Failed to save credential.');
    },
  });

  return (
    <div className="card mb-6 p-5">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold text-slate-800">
          {isEdit ? 'Edit Credential' : 'New Credential'}
        </h2>
        <button onClick={onClose} className="p-1 hover:bg-slate-100 rounded">
          <X size={16} className="text-slate-400" />
        </button>
      </div>

      <form onSubmit={handleSubmit((v) => mutation.mutate(v))} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="label">Name</label>
            <input {...register('name')} className="input" placeholder="e.g. serper-api-key" disabled={isEdit} />
            {errors.name && <p className="text-xs text-red-500 mt-1">{errors.name.message}</p>}
          </div>
          <div>
            <label className="label">Type</label>
            <select {...register('type')} className="input">
              <option value="api_key">API Key</option>
              <option value="bearer">Bearer Token</option>
              <option value="oauth2">OAuth2</option>
              <option value="mtls">mTLS</option>
            </select>
          </div>
        </div>

        <div>
          <label className="label">Team</label>
          <input {...register('owner_team')} className="input" placeholder="e.g. platform" />
        </div>

        <div className="border-t pt-4">
          <p className="text-xs text-slate-400 mb-3">
            Stored as a Kubernetes Secret. Values are write-only — they cannot be retrieved after saving.
          </p>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="label">Key Name</label>
              <input
                {...register('credential_key')}
                className="input"
                placeholder="e.g. serper_api_key"
              />
            </div>
            <div>
              <label className="label">Secret Value</label>
              <input
                {...register('credential_value')}
                type="password"
                className="input"
                placeholder={isEdit ? '(unchanged if blank)' : 'Paste secret value'}
              />
            </div>
          </div>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="btn-secondary">Cancel</button>
          <button type="submit" disabled={mutation.isPending} className="btn-primary">
            {mutation.isPending && <Loader2 size={14} className="animate-spin mr-1" />}
            {isEdit ? 'Update' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}
