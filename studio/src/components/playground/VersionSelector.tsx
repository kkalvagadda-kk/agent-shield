import { useQuery } from "@tanstack/react-query";
import { listAgents } from "../../api/registryApi";
import type { Agent } from "../../api/registryApi";

interface Props {
  selectedAgent: string;
  onSelect: (agentName: string) => void;
}

const CLASS_CHIP: Record<string, string> = {
  daemon:         "bg-blue-100 text-blue-700",
  user_delegated: "bg-purple-100 text-purple-700",
};

export default function VersionSelector({ selectedAgent, onSelect }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["agents-for-playground"],
    queryFn: () => listAgents(100, 0, "active"),
  });

  const agents: Agent[] = data?.items ?? [];

  return (
    <div className="flex flex-col gap-3">
      <label className="label text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Select Agent
      </label>
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading agents…</p>
      ) : (
        <select
          className="input text-sm"
          value={selectedAgent}
          onChange={(e) => onSelect(e.target.value)}
        >
          <option value="">-- pick an agent --</option>
          {agents.map((a) => (
            <option key={a.id} value={a.name}>
              {a.name}
            </option>
          ))}
        </select>
      )}

      {selectedAgent && (() => {
        const agent = agents.find((a) => a.name === selectedAgent);
        if (!agent) return null;
        const agentClass = (agent as Agent & { agent_class?: string }).agent_class;
        return (
          <div className="flex items-center gap-2">
            {agentClass && (
              <span className={`badge text-xs ${CLASS_CHIP[agentClass] ?? "bg-slate-100 text-slate-600"}`}>
                {agentClass}
              </span>
            )}
            {agent.team && (
              <span className="text-xs text-slate-400">Team: {agent.team}</span>
            )}
          </div>
        );
      })()}
    </div>
  );
}
