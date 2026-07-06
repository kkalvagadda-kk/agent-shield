import { useQuery } from '@tanstack/react-query';
import { GitMerge, Loader2, Plus } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';
import { listCompositeWorkflows, type CompositeWorkflow } from '../api/registryApi';

// ---------------------------------------------------------------------------
// Status badge
// ---------------------------------------------------------------------------
const STATUS_BADGE: Record<string, string> = {
  draft: 'bg-slate-100 text-slate-600',
  published: 'bg-green-100 text-green-700',
  archived: 'bg-slate-200 text-slate-500',
};

const ORCHESTRATION_BADGE: Record<string, string> = {
  sequential: 'bg-blue-50 text-blue-700',
  supervisor: 'bg-purple-50 text-purple-700',
  handoff: 'bg-teal-50 text-teal-700',
};

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function WorkflowsPage() {
  const navigate = useNavigate();

  const { data, isLoading, error } = useQuery({
    queryKey: ['composite-workflows'],
    queryFn: () => listCompositeWorkflows(),
  });

  const workflows: CompositeWorkflow[] = data ?? [];

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Workflows</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Compose existing agents into multi-step pipelines
          </p>
        </div>
        <button onClick={() => navigate('/workflows/new')} className="btn-primary">
          <Plus size={14} />
          New Workflow
        </button>
      </div>

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading workflows…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load workflows: {String(error)}
        </div>
      )}

      {/* Content */}
      {!isLoading && !error && (
        workflows.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <GitMerge size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No workflows yet.</p>
            <p className="text-slate-400 text-sm mt-1">
              Create one to compose existing agents.
            </p>
            <button onClick={() => navigate('/workflows/new')} className="btn-primary mt-5">
              <Plus size={14} />
              New Workflow
            </button>
          </div>
        ) : (
          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {['Name', 'Team', 'Orchestration', 'Status', 'Members', 'Updated', ''].map((h) => (
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
                {workflows.map((wf) => {
                  const statusCls =
                    STATUS_BADGE[wf.status] ?? 'bg-slate-100 text-slate-600';
                  const orchCls =
                    ORCHESTRATION_BADGE[wf.orchestration] ?? 'bg-slate-100 text-slate-600';
                  return (
                    <tr key={wf.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3">
                        <Link
                          to={`/workflows/${wf.id}/builder`}
                          className="font-semibold text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          {wf.name}
                        </Link>
                        {wf.description && (
                          <p className="text-xs text-slate-400 truncate max-w-xs mt-0.5">
                            {wf.description}
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-600">{wf.team}</td>
                      <td className="px-4 py-3">
                        <span className={`badge ${orchCls} capitalize`}>
                          {wf.orchestration}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${statusCls} capitalize`}>
                          {wf.status}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-600 text-center">
                        {wf.member_count}
                      </td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {relativeTime(wf.updated_at)}
                      </td>
                      <td className="px-4 py-3 text-right">
                        <button
                          onClick={() => navigate(`/workflows/${wf.id}/builder`)}
                          className="btn-primary py-1.5 text-xs"
                        >
                          Open
                        </button>
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
