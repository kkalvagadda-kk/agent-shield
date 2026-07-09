import { useQuery } from "@tanstack/react-query";
import { listAllDeployments } from "../../api/registryApi";
import type { Deployment } from "../../api/registryApi";

export interface AgentDeploymentSelection {
  agentName: string;
  versionId: string | null;
  deploymentId: string;
}

interface Props {
  selectedAgent: string;
  onSelect: (agentName: string, selection?: AgentDeploymentSelection) => void;
}

export default function VersionSelector({ selectedAgent, onSelect }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["sandbox-deployments-for-playground"],
    queryFn: () => listAllDeployments("running", 100, "sandbox"),
  });

  const deployments: Deployment[] = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <label className="label text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Select Deployment
      </label>
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading deployments…</p>
      ) : deployments.length === 0 ? (
        <p className="text-sm text-amber-600">No running sandbox deployments. Deploy an agent first.</p>
      ) : (
        <select
          className="input text-sm"
          value={selectedAgent}
          onChange={(e) => {
            const name = e.target.value;
            if (!name) { onSelect(""); return; }
            const dep = deployments.find((d) => d.agent_name === name);
            onSelect(name, dep ? { agentName: name, versionId: dep.version_id ?? null, deploymentId: dep.id } : undefined);
          }}
        >
          <option value="">-- pick a deployment --</option>
          {deployments.map((d) => (
            <option key={d.id} value={d.agent_name ?? ""}>
              {d.name ?? d.agent_name ?? d.id.slice(0, 8)} ({d.agent_name})
            </option>
          ))}
        </select>
      )}

      {selectedAgent && (() => {
        const dep = deployments.find((d) => d.agent_name === selectedAgent);
        if (!dep) return null;
        return (
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="badge bg-green-100 text-green-700 text-xs">running</span>
            <span>Deployment: {dep.name ?? dep.id.slice(0, 8)}</span>
          </div>
        );
      })()}
    </div>
  );
}
