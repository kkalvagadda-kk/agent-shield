import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownCircle, ArrowUpCircle, Loader2, Pause, Play, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  listVersions,
  rollbackAgent,
  updateSandboxDeployment,
  type Deployment,
  type DeploymentAction,
} from "../../api/registryApi";

interface Props {
  agentName: string;
  deployment: Deployment;
}

const TRANSITIONAL = new Set(["pending", "deploying", "suspending", "terminating"]);

export default function DeploymentActions({ agentName, deployment }: Props) {
  const qc = useQueryClient();
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState("");

  const { data: versions = [] } = useQuery({
    queryKey: ["versions", agentName],
    queryFn: () => listVersions(agentName),
    enabled: upgradeOpen,
  });

  const act = useMutation({
    mutationFn: ({ action, versionId }: { action: DeploymentAction; versionId?: string }) =>
      updateSandboxDeployment(agentName, deployment.id, action, versionId),
    onSuccess: (_d, vars) => {
      toast.success(`Action "${vars.action}" applied.`);
      setUpgradeOpen(false);
      setSelectedVersion("");
      qc.invalidateQueries({ queryKey: ["deployments", agentName] });
    },
    onError: (_e, vars) => toast.error(`Action "${vars.action}" failed.`),
  });

  const rollbackMut = useMutation({
    mutationFn: () => rollbackAgent(agentName),
    onSuccess: () => {
      toast.success("Rollback initiated — deploying previous version.");
      qc.invalidateQueries({ queryKey: ["deployments", agentName] });
      qc.invalidateQueries({ queryKey: ["versions", agentName] });
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Rollback failed."),
  });

  const status = deployment.status;
  const busy = act.isPending || rollbackMut.isPending;

  if (TRANSITIONAL.has(status)) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-slate-500">
        <Loader2 size={12} className="animate-spin" /> {status}…
      </span>
    );
  }

  const labelBtn = "btn-secondary text-xs inline-flex items-center gap-1 disabled:opacity-50";
  const iconBtn = "p-1.5 rounded-md hover:bg-slate-100 disabled:opacity-40 transition-colors";

  return (
    <>
      <div className="inline-flex items-center gap-1.5">
        {status === "running" && (
          <>
            <button
              onClick={() => { setUpgradeOpen(true); setSelectedVersion(""); }}
              disabled={busy}
              className={labelBtn}
            >
              <ArrowUpCircle size={12} /> Upgrade
            </button>
            {deployment.previous_version_id && (
              <button
                onClick={() => {
                  if (confirm(`Rollback "${deployment.name ?? agentName}" to the previous version?`)) {
                    rollbackMut.mutate();
                  }
                }}
                disabled={busy}
                className={`${labelBtn} text-blue-700`}
              >
                {rollbackMut.isPending ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <><ArrowDownCircle size={12} /> Rollback</>
                )}
              </button>
            )}
            <button
              onClick={() => act.mutate({ action: "suspend" })}
              disabled={busy}
              className={`${iconBtn} text-amber-600`}
              title="Suspend"
            >
              <Pause size={14} />
            </button>
          </>
        )}

        {status === "suspended" && (
          <button
            onClick={() => act.mutate({ action: "resume" })}
            disabled={busy}
            className={`${iconBtn} text-green-600`}
            title="Resume"
          >
            <Play size={14} />
          </button>
        )}

        {status !== "terminated" && (
          <button
            onClick={() => {
              if (confirm(`Terminate deployment "${deployment.name ?? agentName}"? This deletes its pod + service.`)) {
                act.mutate({ action: "terminate" });
              }
            }}
            disabled={busy}
            className={`${iconBtn} text-red-500 hover:bg-red-50`}
            title="Terminate"
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {upgradeOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setUpgradeOpen(false)}>
          <div className="card w-full max-w-sm p-6 bg-white" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-base font-semibold text-slate-900">Upgrade Deployment</h3>
              <button onClick={() => setUpgradeOpen(false)} className="text-slate-400 hover:text-slate-600">
                <X size={16} />
              </button>
            </div>

            <p className="text-xs text-slate-500 mb-3">
              {deployment.name ?? agentName} · currently{" "}
              {versions.find((v) => v.id === deployment.version_id)
                ? `v${versions.find((v) => v.id === deployment.version_id)!.version_number}`
                : "unknown version"}
            </p>

            <label className="block text-sm text-slate-600 mb-1">Target version</label>
            <select
              value={selectedVersion}
              onChange={(e) => setSelectedVersion(e.target.value)}
              className="input w-full text-sm mb-4"
            >
              <option value="">Select version…</option>
              {versions
                .filter((v) => v.id !== deployment.version_id)
                .map((v) => (
                  <option key={v.id} value={v.id}>
                    v{v.version_number}{v.eval_passed ? " ✓ eval passed" : ""}
                  </option>
                ))}
            </select>

            <div className="flex justify-end gap-2">
              <button onClick={() => setUpgradeOpen(false)} className="btn-secondary text-sm">
                Cancel
              </button>
              <button
                onClick={() => {
                  if (selectedVersion) {
                    act.mutate({ action: "upgrade", versionId: selectedVersion });
                  }
                }}
                disabled={!selectedVersion || act.isPending}
                className="btn-primary text-sm"
              >
                {act.isPending ? (
                  <><Loader2 size={13} className="animate-spin" /> Upgrading…</>
                ) : (
                  <><ArrowUpCircle size={13} /> Upgrade</>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
