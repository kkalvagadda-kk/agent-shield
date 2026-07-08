import { useQuery } from "@tanstack/react-query";
import { CheckCircle, Clock, Loader2, XCircle } from "lucide-react";
import { getWorkflowRunTree } from "../../api/registryApi";

interface Props {
  workflowId: string;
  runId: string;
}

export default function WorkflowRunTree({ workflowId, runId }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["workflow-run-tree", workflowId, runId],
    queryFn: () => getWorkflowRunTree(workflowId, runId),
    refetchInterval: (query) => {
      const status = query.state.data?.parent?.status;
      if (status === "completed" || status === "failed") return false;
      return 3000;
    },
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400 py-4">
        <Loader2 size={14} className="animate-spin" />
        Loading run…
      </div>
    );
  }

  if (!data) return null;

  const { parent, children } = data;

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-slate-200 p-3">
        <div className="flex items-center gap-2 mb-2">
          <StatusIcon status={parent.status} />
          <span className="text-sm font-medium text-slate-700">{parent.agent_name}</span>
          <span className="badge bg-slate-100 text-slate-600 text-xs">{parent.status}</span>
        </div>
        {parent.output && (
          <div className="mt-2 bg-slate-50 rounded p-2">
            <p className="text-xs font-semibold text-slate-500 mb-1">Output</p>
            <p className="text-sm text-slate-700 whitespace-pre-wrap">{parent.output}</p>
          </div>
        )}
      </div>

      {children.length > 0 && (
        <div className="ml-4 space-y-2 border-l-2 border-slate-100 pl-3">
          {children.map((child) => (
            <div key={child.id} className="rounded border border-slate-100 p-2">
              <div className="flex items-center gap-2">
                <StatusIcon status={child.status} />
                <span className="text-xs font-medium text-slate-600">{child.agent_name}</span>
                <span className="text-xs text-slate-400">{child.status}</span>
              </div>
              {child.output && (
                <p className="text-xs text-slate-600 mt-1 whitespace-pre-wrap truncate max-h-20 overflow-hidden">
                  {child.output}
                </p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function StatusIcon({ status }: { status: string }) {
  if (status === "completed") return <CheckCircle size={14} className="text-green-500" />;
  if (status === "failed") return <XCircle size={14} className="text-red-500" />;
  if (status === "running" || status === "queued") return <Clock size={14} className="text-blue-500" />;
  return <Clock size={14} className="text-slate-400" />;
}
