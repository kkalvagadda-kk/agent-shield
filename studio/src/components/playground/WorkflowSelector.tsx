import { useQuery } from "@tanstack/react-query";
import { listCompositeWorkflows } from "../../api/registryApi";

interface Props {
  selectedWorkflowId: string;
  onSelect: (wf: { id: string; name: string } | null) => void;
}

export default function WorkflowSelector({ selectedWorkflowId, onSelect }: Props) {
  const { data: workflows, isLoading } = useQuery({
    queryKey: ["workflows-for-playground"],
    queryFn: () => listCompositeWorkflows(),
  });

  const active = workflows?.filter((w) => w.status !== "archived") ?? [];

  return (
    <div className="flex flex-col gap-3">
      <label className="label text-xs font-semibold text-slate-500 uppercase tracking-wider">
        Select Workflow
      </label>
      {isLoading ? (
        <p className="text-sm text-slate-400">Loading workflows…</p>
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
            const wf = active.find((w) => w.id === id);
            onSelect(wf ? { id: wf.id, name: wf.name } : null);
          }}
        >
          <option value="">-- pick a workflow --</option>
          {active.map((w) => (
            <option key={w.id} value={w.id}>
              {w.name}
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
