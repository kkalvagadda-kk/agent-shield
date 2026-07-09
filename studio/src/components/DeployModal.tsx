import { useMutation } from "@tanstack/react-query";
import { Loader2, Rocket, X } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { deployAgent, type Deployment } from "../api/registryApi";

interface Props {
  agentName: string;
  versionId?: string;
  versionLabel?: string;
  onClose: () => void;
  onDeployed?: (dep: Deployment) => void;
}

/**
 * Small deploy-config modal. Every deploy creates a NEW sandbox deployment —
 * replicas + an optional TTL (auto-terminate window). Used from the agent list
 * and from a Versions-tab row.
 */
export default function DeployModal({ agentName, versionId, versionLabel, onClose, onDeployed }: Props) {
  const navigate = useNavigate();
  const [replicas, setReplicas] = useState(1);
  const [ttlHours, setTtlHours] = useState<string>("");

  const deploy = useMutation({
    mutationFn: () =>
      deployAgent(agentName, {
        version_id: versionId || undefined,
        environment: "sandbox",
        replicas,
        ttl_hours: ttlHours ? Number(ttlHours) : undefined,
      }),
    onSuccess: (dep) => {
      toast.success(`Deploying ${dep.name ?? agentName}…`);
      onDeployed?.(dep);
      onClose();
      navigate(`/agents/${agentName}/d/${dep.id}`);
    },
    onError: (e: unknown) => toast.error(e instanceof Error ? e.message : "Deploy failed."),
  });

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={onClose}>
      <div className="card w-full max-w-md p-6 bg-white" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">Deploy to sandbox</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600">
            <X size={18} />
          </button>
        </div>

        <p className="text-xs text-slate-500 mb-4">
          {agentName}
          {versionLabel ? ` · ${versionLabel}` : ""} — creates a new sandbox deployment.
        </p>

        <div className="space-y-4">
          <div>
            <label className="label">Replicas</label>
            <input
              type="number"
              min={1}
              max={10}
              value={replicas}
              onChange={(e) => setReplicas(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
              className="input"
            />
          </div>
          <div>
            <label className="label">Auto-terminate after (hours)</label>
            <input
              type="number"
              min={1}
              placeholder="Never"
              value={ttlHours}
              onChange={(e) => setTtlHours(e.target.value)}
              className="input"
            />
            <p className="text-xs text-slate-400 mt-1">Leave blank to keep the deployment until you terminate it.</p>
          </div>
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <button onClick={onClose} className="btn-secondary text-sm">Cancel</button>
          <button
            onClick={() => deploy.mutate()}
            disabled={deploy.isPending}
            className="btn-primary text-sm"
          >
            {deploy.isPending ? (
              <><Loader2 size={14} className="animate-spin" /> Deploying…</>
            ) : (
              <><Rocket size={14} /> Deploy</>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
