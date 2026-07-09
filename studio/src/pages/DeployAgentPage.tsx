import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  ArrowLeft,
  CheckCircle2,
  ChevronRight,
  Loader2,
  Rocket,
  XCircle,
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  deployAgent,
  getAgent,
  getDeployments,
  listVersions,
  type Deployment,
} from "../api/registryApi";

const STATUS: Record<string, { label: string; cls: string; icon?: JSX.Element }> = {
  pending:     { label: "Pending",     cls: "bg-amber-100 text-amber-700",  icon: <Loader2 size={11} className="animate-spin" /> },
  deploying:   { label: "Deploying",  cls: "bg-blue-100 text-blue-700",    icon: <Loader2 size={11} className="animate-spin" /> },
  running:     { label: "Running",    cls: "bg-green-100 text-green-700",  icon: <CheckCircle2 size={11} /> },
  failed:      { label: "Failed",     cls: "bg-red-100 text-red-700",      icon: <XCircle size={11} /> },
  rolled_back: { label: "Rolled back", cls: "bg-slate-100 text-slate-600" },
  terminated:  { label: "Terminated", cls: "bg-slate-100 text-slate-600" },
};

const TERMINAL = new Set(["running", "failed", "rolled_back", "terminated"]);

export default function DeployAgentPage() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();
  const [polling, setPolling] = useState(false);
  const pollerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data: agent } = useQuery({
    queryKey: ["agent", name],
    queryFn: () => getAgent(name!),
    enabled: !!name,
  });

  const { data: versions, refetch: refetchVersions } = useQuery({
    queryKey: ["versions", name],
    queryFn: () => listVersions(name!),
    enabled: !!name,
  });

  const { data: deployments, refetch: refetchDeployments } = useQuery({
    queryKey: ["deployments", name],
    queryFn: () => getDeployments(name!),
    enabled: !!name,
  });

  useEffect(() => {
    if (!polling) return;
    pollerRef.current = setInterval(async () => {
      const result = await refetchDeployments();
      const latest = result.data?.[0];
      if (latest && TERMINAL.has(latest.status)) {
        setPolling(false);
        if (pollerRef.current) clearInterval(pollerRef.current);
        if (latest.status === "running") {
          toast.success("Deployment is running.");
        } else {
          toast.error(`Deployment ${latest.status}.${latest.error_message ? ` ${latest.error_message}` : ""}`);
        }
      }
    }, 5_000);
    return () => { if (pollerRef.current) clearInterval(pollerRef.current); };
  }, [polling, refetchDeployments]);

  const deployMutation = useMutation({
    mutationFn: async () => {
      // Deploy without version_id — backend auto-creates a new version
      // that snapshots current agent metadata (instructions, tools, model)
      return deployAgent(name!, { environment: "sandbox" });
    },
    onSuccess: () => {
      toast.info("Sandbox deployment triggered — polling for status…");
      setPolling(true);
      refetchVersions();
      refetchDeployments();
    },
    onError: (e: unknown) =>
      toast.error(e instanceof Error ? e.message : "Deploy failed."),
  });

  const latestDeployment: Deployment | undefined = deployments?.[0];

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <button
        onClick={() => navigate("/")}
        className="inline-flex items-center gap-1.5 text-sm text-slate-500 hover:text-slate-900 mb-6 transition-colors"
      >
        <ArrowLeft size={14} />
        Back to agents
      </button>

      {/* Agent header */}
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{name}</h1>
          {agent && (
            <p className="text-sm text-slate-500 mt-0.5">
              {agent.team} · {agent.agent_type} · {agent.status}
              {agent.description && <> · {agent.description}</>}
            </p>
          )}
        </div>
      </div>

      <div className="space-y-4">
        {/* Deploy */}
        <div className="card">
          <div className="flex items-center gap-2 mb-4">
            <div className="w-6 h-6 rounded-full bg-blue-600 text-white text-xs font-bold flex items-center justify-center">
              1
            </div>
            <h2 className="font-semibold text-slate-900">Deploy to Sandbox</h2>
          </div>

          <p className="text-xs text-slate-500 mb-3">
            Creates a new version snapshot of current agent config and deploys it.
          </p>

          <button
            onClick={() => deployMutation.mutate()}
            disabled={deployMutation.isPending || polling}
            className="btn-primary"
          >
            {deployMutation.isPending ? (
              <><Loader2 size={14} className="animate-spin" /> Deploying…</>
            ) : (
              <><Rocket size={14} /> Deploy</>
            )}
          </button>

          {polling && (
            <p className="mt-3 text-sm text-slate-500 flex items-center gap-1.5">
              <Loader2 size={13} className="animate-spin text-blue-500" />
              Polling for status every 5s…
            </p>
          )}
        </div>

        {/* Version history */}
        {versions && versions.length > 0 && (
          <div className="card">
            <h2 className="font-semibold text-slate-900 mb-4">Versions</h2>
            <div className="divide-y divide-slate-100 rounded-lg border border-slate-100 overflow-hidden">
              {versions.slice(0, 6).map((v) => (
                <div
                  key={v.id}
                  className="flex items-center gap-3 px-3 py-2 text-sm bg-slate-50"
                >
                  <span className="font-mono text-slate-700 text-xs shrink-0">
                    v{v.version_number}
                  </span>
                  <span className="text-slate-400 text-xs truncate flex-1">
                    {v.image_tag ?? "config snapshot"}
                  </span>
                  <span
                    className={`badge shrink-0 ${
                      v.eval_passed ? "bg-green-100 text-green-700" : "bg-amber-100 text-amber-700"
                    }`}
                  >
                    {v.eval_passed ? "eval passed" : "eval pending"}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Deployment history */}
        {deployments && deployments.length > 0 && (
          <div className="card">
            <h2 className="font-semibold text-slate-900 mb-4">Deployment History</h2>
            <div className="space-y-2">
              {deployments.slice(0, 8).map((d: Deployment, i) => {
                const s = STATUS[d.status] ?? { label: d.status, cls: "bg-slate-100 text-slate-600" };
                return (
                  <div
                    key={d.id}
                    className={`flex items-center gap-3 p-3 rounded-lg text-sm ${
                      i === 0 ? "bg-slate-50 ring-1 ring-slate-200" : ""
                    }`}
                  >
                    <span className={`badge gap-1 shrink-0 ${s.cls}`}>
                      {s.icon}
                      {s.label}
                    </span>
                    <span className="text-slate-500 text-xs">{d.environment}</span>
                    <ChevronRight size={12} className="text-slate-300" />
                    <span className="text-slate-400 text-xs">
                      {new Date(d.deployed_at).toLocaleString()}
                    </span>
                    {d.error_message && (
                      <span className="ml-auto text-red-500 text-xs truncate max-w-xs">
                        {d.error_message}
                      </span>
                    )}
                    {i === 0 && (
                      <span className="ml-auto text-xs font-medium text-blue-600 bg-blue-50 rounded px-1.5 py-0.5">
                        Latest
                      </span>
                    )}
                  </div>
                );
              })}
            </div>
            {latestDeployment?.k8s_deployment_name && (
              <p className="mt-3 text-xs text-slate-400 font-mono">
                k8s: {latestDeployment.k8s_deployment_name} · ns: {latestDeployment.k8s_namespace}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
