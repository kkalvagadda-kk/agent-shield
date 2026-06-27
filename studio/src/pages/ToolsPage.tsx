import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { zodResolver } from '@hookform/resolvers/zod';
import { Code, Loader2, Pencil, Plus, Trash2, Wrench, X } from 'lucide-react';
import { useState } from 'react';
import { useForm } from 'react-hook-form';
import { toast } from 'sonner';
import { z } from 'zod';
import {
  createTool,
  deleteTool,
  listTools,
  updateTool,
  type CreateToolPayload,
  type RegistryTool,
} from '../api/registryApi';
import { cn } from '../lib/utils';

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------
const baseSchema = z.object({
  name: z
    .string()
    .min(1, 'Name is required')
    .regex(/^[a-z0-9_]+$/, 'Only lowercase letters, numbers, and underscores'),
  display_name: z.string().optional(),
  description: z.string().optional(),
  tool_type: z.enum(['http', 'python']),
  risk_level: z.enum(['low', 'medium', 'high']),
  owner_team: z.string().optional(),
  http_method: z.enum(['GET', 'POST', 'PUT', 'DELETE']).optional(),
  http_url: z.string().optional(),
  python_code: z.string().optional(),
});

const schema = baseSchema.superRefine((data, ctx) => {
  if (data.tool_type === 'http') {
    if (!data.http_url || data.http_url.trim() === '') {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'URL is required', path: ['http_url'] });
    }
    if (!data.http_method) {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Method is required', path: ['http_method'] });
    }
  }
  if (data.tool_type === 'python') {
    if (!data.python_code || data.python_code.trim() === '') {
      ctx.addIssue({ code: z.ZodIssueCode.custom, message: 'Python code is required', path: ['python_code'] });
    }
  }
});

type FormValues = z.infer<typeof baseSchema>;

const PYTHON_STARTER = `def run_tool(args: dict) -> str:
    """
    Tool logic here.
    args: dict of arguments the LLM provides at call time.
    Return a string result.
    """
    return str(args)
`;

// ---------------------------------------------------------------------------
// Risk badge
// ---------------------------------------------------------------------------
const RISK_BADGE: Record<string, string> = {
  low: 'bg-green-100 text-green-700',
  medium: 'bg-amber-100 text-amber-700',
  high: 'bg-red-100 text-red-700',
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ToolsPage() {
  const qc = useQueryClient();
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [editingTool, setEditingTool] = useState<RegistryTool | null>(null);

  const { data, isLoading, error } = useQuery({
    queryKey: ['registry-tools'],
    queryFn: () => listTools(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteTool(id),
    onSuccess: () => {
      toast.success('Tool deleted.');
      qc.invalidateQueries({ queryKey: ['registry-tools'] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? 'Failed to delete tool.');
    },
  });

  const tools: RegistryTool[] = data?.items ?? [];

  const openCreate = () => {
    setEditingTool(null);
    setShowCreateForm(true);
  };

  const openEdit = (tool: RegistryTool) => {
    setShowCreateForm(false);
    setEditingTool(tool);
  };

  const closeForm = () => {
    setShowCreateForm(false);
    setEditingTool(null);
  };

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Tools</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Register HTTP and Python tools that agents can call
          </p>
        </div>
        <button onClick={openCreate} className="btn-primary">
          <Plus size={14} />
          New Tool
        </button>
      </div>

      {/* Create / Edit form */}
      {(showCreateForm || editingTool) && (
        <ToolForm
          tool={editingTool}
          onClose={closeForm}
          onSaved={() => {
            closeForm();
            qc.invalidateQueries({ queryKey: ['registry-tools'] });
          }}
        />
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading tools…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load tools: {String(error)}
        </div>
      )}

      {/* Content */}
      {!isLoading && !error && (
        tools.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <Wrench size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No tools registered yet.</p>
            <button onClick={openCreate} className="btn-primary mt-5">
              <Plus size={14} />
              New Tool
            </button>
          </div>
        ) : (
          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {['Name', 'Type', 'Risk', 'Team', 'Status', ''].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {tools.map((tool) => {
                  const riskCls =
                    RISK_BADGE[tool.risk_level ?? 'low'] ??
                    'bg-slate-100 text-slate-600';
                  return (
                    <tr key={tool.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          {tool.type === 'python' ? (
                            <Code size={13} className="text-purple-500 shrink-0" />
                          ) : (
                            <Wrench size={13} className="text-slate-400 shrink-0" />
                          )}
                          <p className="font-semibold text-slate-900">
                            {tool.display_name ?? tool.name}
                          </p>
                        </div>
                        {tool.description && (
                          <p className="text-xs text-slate-400 truncate max-w-xs mt-0.5 pl-5">
                            {tool.description}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className={cn('badge', tool.type === 'python' ? 'bg-purple-100 text-purple-700' : 'bg-slate-100 text-slate-600')}>
                          {tool.type}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge capitalize ${riskCls}`}>
                          {tool.risk_level ?? '—'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {tool.owner_team ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-slate-600">
                        {tool.status ?? '—'}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <div className="flex items-center justify-end gap-3">
                          <button
                            onClick={() => openEdit(tool)}
                            className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 transition-colors"
                          >
                            <Pencil size={12} />
                            Edit
                          </button>
                          <button
                            onClick={() => {
                              if (confirm(`Delete tool "${tool.display_name ?? tool.name}"?`)) {
                                deleteMutation.mutate(tool.id);
                              }
                            }}
                            disabled={
                              deleteMutation.isPending &&
                              deleteMutation.variables === tool.id
                            }
                            className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
                          >
                            {deleteMutation.isPending &&
                            deleteMutation.variables === tool.id ? (
                              <Loader2 size={12} className="animate-spin" />
                            ) : (
                              <Trash2 size={12} />
                            )}
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / Edit form
// ---------------------------------------------------------------------------
function ToolForm({
  tool,
  onClose,
  onSaved,
}: {
  tool: RegistryTool | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = tool !== null;

  const {
    register,
    handleSubmit,
    watch,
    formState: { errors, isSubmitting },
  } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: isEdit
      ? {
          name: tool.name,
          display_name: tool.display_name ?? '',
          description: tool.description ?? '',
          tool_type: (tool.type === 'python' ? 'python' : 'http') as 'http' | 'python',
          risk_level: (tool.risk_level ?? 'low') as 'low' | 'medium' | 'high',
          owner_team: tool.owner_team ?? '',
          http_method: (tool.http_method ?? 'GET') as 'GET' | 'POST' | 'PUT' | 'DELETE',
          http_url: tool.http_url ?? '',
          python_code: tool.python_code ?? PYTHON_STARTER,
        }
      : {
          tool_type: 'http',
          http_method: 'GET',
          risk_level: 'low',
          python_code: PYTHON_STARTER,
        },
  });

  const toolType = watch('tool_type');

  const mutation = useMutation({
    mutationFn: (values: FormValues) => {
      if (isEdit) {
        const payload: Partial<CreateToolPayload & { display_name: string; description: string; owner_team: string }> = {
          display_name: values.display_name,
          description: values.description,
          risk_level: values.risk_level,
          owner_team: values.owner_team,
          ...(values.tool_type === 'http'
            ? { http_method: values.http_method ?? 'GET', http_url: values.http_url }
            : { python_code: values.python_code }),
        };
        return updateTool(tool.id, payload);
      }
      const payload: CreateToolPayload = {
        name: values.name,
        type: values.tool_type,
        risk_level: values.risk_level,
        ...(values.display_name ? { display_name: values.display_name } : {}),
        ...(values.description ? { description: values.description } : {}),
        ...(values.owner_team ? { owner_team: values.owner_team } : {}),
        ...(values.tool_type === 'http'
          ? { http_method: values.http_method ?? 'GET', http_url: values.http_url }
          : { python_code: values.python_code }),
      };
      return createTool(payload);
    },
    onSuccess: () => {
      toast.success(isEdit ? 'Tool updated.' : 'Tool created.');
      onSaved();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? (isEdit ? 'Failed to update tool.' : 'Failed to create tool.'));
    },
  });

  return (
    <div className="card mb-6 relative">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"
      >
        <X size={16} />
      </button>
      <h2 className="text-lg font-semibold text-slate-900 mb-5">
        {isEdit ? `Edit Tool — ${tool.name}` : 'New Tool'}
      </h2>

      <form
        onSubmit={handleSubmit((v) => mutation.mutate(v))}
        className="space-y-4"
        noValidate
      >
        {/* Tool type — read-only in edit mode */}
        <Field label="Tool Type" required>
          <div className="flex gap-3">
            {(['http', 'python'] as const).map((t) => (
              <label
                key={t}
                className={cn(
                  'flex items-center gap-2',
                  isEdit ? 'cursor-not-allowed opacity-60' : 'cursor-pointer'
                )}
              >
                <input
                  type="radio"
                  value={t}
                  {...register('tool_type')}
                  disabled={isEdit}
                  className="accent-blue-600"
                />
                <span className="text-sm font-medium text-slate-700 capitalize">{t}</span>
                {t === 'python' && (
                  <span className="badge bg-purple-100 text-purple-700 text-xs">sandboxed</span>
                )}
              </label>
            ))}
          </div>
          {isEdit && (
            <p className="text-xs text-slate-400 mt-0.5">Tool type cannot be changed after creation.</p>
          )}
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Name" required error={errors.name?.message}>
            <input
              {...register('name')}
              readOnly={isEdit}
              className={cn(
                'input font-mono',
                errors.name && 'border-red-400',
                isEdit && 'bg-slate-50 text-slate-500 cursor-not-allowed'
              )}
              placeholder="get_order_status"
            />
            {!isEdit && (
              <p className="text-xs text-slate-400 mt-0.5">
                Lowercase letters, numbers, underscores only
              </p>
            )}
          </Field>
          <Field label="Display Name" error={errors.display_name?.message}>
            <input
              {...register('display_name')}
              className="input"
              placeholder="Get Order Status"
            />
          </Field>
        </div>

        <Field label="Description" error={errors.description?.message}>
          <input
            {...register('description')}
            className="input"
            placeholder="Retrieves the current status of an order"
          />
        </Field>

        <div className="grid grid-cols-2 gap-4">
          <Field label="Risk Level" required error={errors.risk_level?.message}>
            <select {...register('risk_level')} className="input">
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </Field>
          <Field label="Team" error={errors.owner_team?.message}>
            <input
              {...register('owner_team')}
              className="input"
              placeholder="platform-team"
            />
          </Field>
        </div>

        {/* HTTP-specific fields */}
        {toolType === 'http' && (
          <>
            <div className="grid grid-cols-2 gap-4">
              <Field label="Method" required error={errors.http_method?.message}>
                <select {...register('http_method')} className="input">
                  <option value="GET">GET</option>
                  <option value="POST">POST</option>
                  <option value="PUT">PUT</option>
                  <option value="DELETE">DELETE</option>
                </select>
              </Field>
            </div>

            <Field label="URL" required error={errors.http_url?.message}>
              <input
                {...register('http_url')}
                className={cn('input font-mono', errors.http_url && 'border-red-400')}
                placeholder="https://api.example.com/orders/{{order_id}}"
              />
              <p className="text-xs text-slate-400 mt-0.5">
                Use {'{{variable}}'} placeholders for LLM-provided arguments
              </p>
            </Field>
          </>
        )}

        {/* Python-specific fields */}
        {toolType === 'python' && (
          <Field
            label="Python Code"
            required
            error={errors.python_code?.message}
          >
            <p className="text-xs text-slate-500 mb-1.5">
              Define <code className="font-mono bg-slate-100 px-1 rounded">run_tool(args: dict) -&gt; str</code>.
              Executed in a sandboxed subprocess by the python-executor service.
            </p>
            <textarea
              {...register('python_code')}
              rows={12}
              className={cn(
                'input font-mono text-xs leading-relaxed resize-y',
                errors.python_code && 'border-red-400'
              )}
              spellCheck={false}
            />
          </Field>
        )}

        <div className="flex justify-end gap-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={onClose} className="btn-secondary">
            Cancel
          </button>
          <button
            type="submit"
            disabled={isSubmitting || mutation.isPending}
            className="btn-primary"
          >
            {isSubmitting || mutation.isPending ? (
              <>
                <Loader2 size={14} className="animate-spin" /> Saving…
              </>
            ) : isEdit ? (
              'Save Changes'
            ) : (
              'Create Tool'
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

function Field({
  label,
  required,
  error,
  children,
}: {
  label: string;
  required?: boolean;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="label">
        {label}
        {required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
      {error && <p className="text-xs text-red-600">{error}</p>}
    </div>
  );
}
