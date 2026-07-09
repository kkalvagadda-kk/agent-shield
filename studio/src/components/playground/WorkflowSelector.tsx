import { useQuery } from "@tanstack/react-query";
import { listAllWorkflowDeployments } from "../../api/registryApi";

export interface WorkflowDeploymentSelection {
  id: string;
  name: string;
  versionId: string | null;
  deploymentId: string;
}

interface Props {
  selectedWorkflowId: string;
  onSelect: (wf: WorkflowDeploymentSelection | null) => void;
}

export default function WorkflowSelector({ selectedWorkflowId, onSelect }: Props) {
  const { data: deployments, isLoading } = useQuery({
    queryKey: ["sandbox-workflow-deployments-for-playground"],
    queryFn: () => listAllWorkflowDeployments("running", "sandbox"),
  });

  const items = deployments ?? [];

  return (
    <div className="flex flex-col gap-3">
      <label className="label text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Select Workflow Deployment
      </label>
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading workflow deployments…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-amber-600">No running sandbox workflow deployments.</p>
      ) : (
        <select
          className="input text-sm"
          value={selectedWorkflowId}
          onChange={(e) => {
            const id = e.target.value;
            if (!id) {
              onSelect(null);
              return;
            }
            const dep = items.find((d) => d.workflow_id === id);
            onSelect(dep ? { id: dep.workflow_id, name: dep.workflow_name ?? dep.name ?? id.slice(0, 8), versionId: dep.version_id ?? null, deploymentId: dep.id } : null);
          }}
        >
          <option value="">-- pick a workflow deployment --</option>
          {items.map((d) => (
            <option key={d.id} value={d.workflow_id}>
              {d.name ?? d.workflow_name ?? d.id.slice(0, 8)} ({d.workflow_name})
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
