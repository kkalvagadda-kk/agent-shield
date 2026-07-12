import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  ArrowUpCircle,
  ChevronDown,
  ChevronRight,
  Clock,
  Copy,
  DollarSign,
  ExternalLink,
  Loader2,
  MessageCircle,
  Pause,
  Play,
  Rocket,
  Eye,
  Trash2,
  Users,
  X,
} from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import {
  getCatalogDetail,
  getCatalogStats,
  deployVersion,
  updateDeployment,
  listCatalogRuns,
  CatalogVersion,
  CatalogDeployment,
  CatalogRun,
  CatalogStats,
  MemberTopologyEntry,
} from "../api/catalogApi";
import TraceDrawer from "../components/playground/TraceDrawer";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-100 text-yellow-700",
  deploying: "bg-blue-100 text-blue-700",
  running: "bg-green-100 text-green-700",
  suspending: "bg-amber-100 text-amber-700",
  suspended: "bg-slate-100 text-slate-600",
  terminating: "bg-red-100 text-red-700",
  failed: "bg-red-100 text-red-700",
  terminated: "bg-red-50 text-red-500",
};

type Tab = "overview" | "runs" | "versions" | "settings";

export default function CatalogDetailPage() {
  const { artifactId } = useParams<{ artifactId: string }>();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<Tab>("overview");

  const { data, isLoading } = useQuery({
    queryKey: ["catalog-detail", artifactId],
    queryFn: () => getCatalogDetail(artifactId!),
    enabled: !!artifactId,
    refetchInterval: 5_000,
  });

  const { data: stats } = useQuery({
    queryKey: ["catalog-stats", artifactId],
    queryFn: () => getCatalogStats(artifactId!),
    enabled: !!artifactId,
    refetchInterval: 30_000,
  });

  const { data: runs, isLoading: runsLoading } = useQuery({
    queryKey: ["catalog-runs", artifactId],
    queryFn: () => listCatalogRuns(artifactId!),
    enabled: !!artifactId && activeTab === "runs",
  });

  const deployMutation = useMutation({
    mutationFn: (versionId: string) => deployVersion(artifactId!, versionId),
    onSuccess: () => {
      toast.success("Deployment initiated");
      queryClient.invalidateQueries({ queryKey: ["catalog-detail", artifactId] });
    },
  });

  const updateMutation = useMutation({
    mutationFn: (params: {
      deploymentId: string;
      action: "upgrade" | "suspend" | "resume" | "terminate";
      versionId?: string;
    }) =>
      updateDeployment(
        artifactId!,
        params.deploymentId,
        params.action,
        params.versionId
      ),
    onSuccess: (_data, vars) => {
      toast.success(`Action "${vars.action}" applied`);
      queryClient.invalidateQueries({ queryKey: ["catalog-detail", artifactId] });
    },
    onError: (_err, vars) => {
      toast.error(`Action "${vars.action}" failed`);
    },
  });

  if (isLoading || !data) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 size={20} className="animate-spin mr-2" /> Loading…
      </div>
    );
  }

  const { artifact, versions, deployments, granted_teams, member_topology } = data;
  const activeDeployment = deployments.find(
    (d) => d.status === "running" || d.status === "deploying" || d.status === "suspended" || d.status === "suspending" || d.status === "pending"
  );

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="mb-6">
        <Link
          to="/catalog"
          className="text-sm text-slate-500 hover:text-slate-700 flex items-center gap-1 mb-3"
        >
          <ArrowLeft size={14} /> Back to Catalog
        </Link>
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">{artifact.name}</h1>
            {artifact.description && (
              <p className="text-sm text-slate-500 mt-1">{artifact.description}</p>
            )}
            <div className="flex items-center gap-3 mt-2 text-xs text-slate-400">
              <span>Type: <span className="font-medium text-slate-600">{artifact.type}</span></span>
              <span>Team: <span className="font-medium text-slate-600">{artifact.team}</span></span>
            </div>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="border-b border-slate-200 mb-6">
        <nav className="flex gap-6 -mb-px">
          {(["overview", "runs", "versions", "settings"] as Tab[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`pb-2 text-sm font-medium capitalize border-b-2 transition-colors ${
                activeTab === tab
                  ? "border-blue-500 text-blue-600"
                  : "border-transparent text-slate-500 hover:text-slate-700"
              }`}
            >
              {tab}
            </button>
          ))}
        </nav>
      </div>

      {/* Content */}
      {activeTab === "overview" && (
        <OverviewTab
          artifact={artifact}
          deployment={activeDeployment || null}
          allDeployments={deployments}
          versions={versions}
          stats={stats || null}
          memberTopology={member_topology || []}
          onAction={(deploymentId, action, versionId) =>
            updateMutation.mutate({ deploymentId, action, versionId })
          }
          onDeploy={(vId) => deployMutation.mutate(vId)}
          isActing={updateMutation.isPending || deployMutation.isPending}
        />
      )}
      {activeTab === "runs" && (
        <RunsTab runs={runs || []} isLoading={runsLoading} />
      )}
      {activeTab === "versions" && (
        <VersionsTab
          versions={versions}
          onDeploy={(vId) => deployMutation.mutate(vId)}
          isDeploying={deployMutation.isPending}
          membersReady={!data.member_topology?.length || data.member_topology.every((m) => m.has_production_deployment)}
        />
      )}
      {activeTab === "settings" && (
        <SettingsTab
          artifact={artifact}
          deployment={activeDeployment || null}
          grantedTeams={granted_teams}
          versions={versions}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Overview Tab
// ---------------------------------------------------------------------------
function OverviewTab({
  artifact,
  deployment,
  allDeployments,
  versions,
  stats,
  memberTopology,
  onAction,
  onDeploy,
  isActing,
}: {
  artifact: { id: string; name: string; type: string; team: string; description: string | null };
  deployment: CatalogDeployment | null;
  allDeployments: CatalogDeployment[];
  versions: CatalogVersion[];
  stats: CatalogStats | null;
  memberTopology: MemberTopologyEntry[];
  onAction: (id: string, action: "suspend" | "resume" | "terminate" | "upgrade", vId?: string) => void;
  onDeploy: (versionId: string) => void;
  isActing: boolean;
}) {
  const [upgradeOpen, setUpgradeOpen] = useState(false);
  const [selectedVersion, setSelectedVersion] = useState("");

  const isWorkflow = artifact.type === "workflow";
  const membersReady = !isWorkflow || memberTopology.every((m) => m.has_production_deployment);

  // Derive execution_shape from the active version's config_snapshot
  const activeVersion = versions.find((v) => v.id === deployment?.version_id) || versions[0];
  const executionShape = (activeVersion?.config_snapshot?.execution_shape as string) || "reactive";

  const ns = deployment?.namespace || `production-${artifact.name}`;
  const k8sName = `${artifact.name}-production`;
  const internalBase = `http://${k8sName}.${ns}:8080`;
  const externalBase = `https://agentshield.127.0.0.1.nip.io:8443/api/v1/agents/${artifact.name}`;

  // Internal endpoints filtered by execution_shape
  const endpoints: { label: string; value: string; method: string }[] = [];
  if (executionShape === "reactive") {
    endpoints.push(
      { label: "Chat", value: `${internalBase}/chat`, method: "POST" },
      { label: "Chat (stream)", value: `${internalBase}/chat/stream`, method: "POST" },
    );
  } else if (executionShape === "durable") {
    endpoints.push(
      { label: "Run", value: `${internalBase}/run`, method: "POST" },
      { label: "Status", value: `${internalBase}/run/{run_id}`, method: "GET" },
    );
  } else if (executionShape === "scheduled") {
    endpoints.push(
      { label: "Trigger", value: `${internalBase}/trigger`, method: "POST" },
    );
  }
  endpoints.push({ label: "Health", value: `${internalBase}/health`, method: "GET" });

  return (
    <div className="space-y-6">
      {/* Metrics Cards */}
      {stats && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            icon={<Activity size={16} className="text-blue-500" />}
            label="Runs (24h)"
            value={String(stats.run_count)}
          />
          <StatCard
            icon={<Clock size={16} className="text-purple-500" />}
            label="P50 Latency"
            value={stats.p50_latency_ms != null ? `${stats.p50_latency_ms}ms` : "—"}
          />
          <StatCard
            icon={<AlertTriangle size={16} className="text-amber-500" />}
            label="Error Rate"
            value={`${(stats.error_rate * 100).toFixed(1)}%`}
          />
          <StatCard
            icon={<DollarSign size={16} className="text-green-500" />}
            label="Cost (24h)"
            value={stats.total_cost_usd > 0 ? `$${stats.total_cost_usd.toFixed(4)}` : "—"}
          />
        </div>
      )}

      {/* Deployment Status Card */}
      {deployment ? (
        <div className="card p-5">
          <div className="flex items-center justify-between mb-4">
            <div className="flex items-center gap-3">
              <span className={`badge ${STATUS_COLORS[deployment.status] || "bg-slate-100 text-slate-600"}`}>
                {deployment.status}
              </span>
              <span className="text-sm font-medium text-slate-700">
                {deployment.version_label || "—"}
              </span>
              <span className="text-xs text-slate-400">
                ns: {deployment.namespace || ns}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {(deployment.status === "suspending" || deployment.status === "terminating" || deployment.status === "deploying") && (
                <span className="text-xs text-slate-500 flex items-center gap-1">
                  <Loader2 size={12} className="animate-spin" />
                  {deployment.status === "suspending" ? "Suspending…" :
                   deployment.status === "terminating" ? "Terminating…" : "Deploying…"}
                </span>
              )}
              {deployment.status === "running" && (
                <>
                  <button
                    onClick={() => setUpgradeOpen(!upgradeOpen)}
                    className="btn-secondary text-xs flex items-center gap-1"
                  >
                    <ArrowUpCircle size={12} /> Upgrade
                  </button>
                  <button
                    onClick={() => onAction(deployment.id, "suspend")}
                    disabled={isActing}
                    className="p-1.5 rounded-md hover:bg-slate-100 disabled:opacity-40 transition-colors text-amber-600"
                    title="Suspend"
                  >
                    <Pause size={14} />
                  </button>
                </>
              )}
              {deployment.status === "suspended" && (
                <button
                  onClick={() => onAction(deployment.id, "resume")}
                  disabled={isActing}
                  className="p-1.5 rounded-md hover:bg-slate-100 disabled:opacity-40 transition-colors text-green-600"
                  title="Resume"
                >
                  <Play size={14} />
                </button>
              )}
              {(deployment.status === "running" || deployment.status === "suspended" || deployment.status === "failed") && (
                <button
                  onClick={() => {
                    if (confirm(`Terminate deployment of "${artifact.name}"? This will delete the K8s pod and service.`)) {
                      onAction(deployment.id, "terminate");
                    }
                  }}
                  disabled={isActing}
                  className="p-1.5 rounded-md hover:bg-red-50 disabled:opacity-40 transition-colors text-red-500"
                  title="Terminate"
                >
                  <Trash2 size={14} />
                </button>
              )}
            </div>
          </div>

          {/* Upgrade modal */}
          {upgradeOpen && (
            <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40" onClick={() => setUpgradeOpen(false)}>
              <div className="card w-full max-w-sm p-6 bg-white" onClick={(e) => e.stopPropagation()}>
                <div className="flex items-center justify-between mb-4">
                  <h3 className="text-base font-semibold text-slate-900">Upgrade Production Deployment</h3>
                  <button onClick={() => setUpgradeOpen(false)} className="text-slate-400 hover:text-slate-600">
                    <X size={16} />
                  </button>
                </div>
                <p className="text-xs text-slate-500 mb-3">
                  {artifact.name} · currently {deployment.version_label || "unknown"}
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
                        {v.version_label} — promoted {new Date(v.promoted_at).toLocaleDateString()}
                      </option>
                    ))}
                </select>
                <div className="flex justify-end gap-2">
                  <button onClick={() => setUpgradeOpen(false)} className="btn-secondary text-sm">Cancel</button>
                  <button
                    onClick={() => {
                      if (selectedVersion) {
                        onAction(deployment.id, "upgrade", selectedVersion);
                        setUpgradeOpen(false);
                      }
                    }}
                    disabled={!selectedVersion || isActing}
                    className="btn-primary text-sm"
                  >
                    {isActing ? (
                      <><Loader2 size={13} className="animate-spin" /> Upgrading…</>
                    ) : (
                      <><ArrowUpCircle size={13} /> Upgrade</>
                    )}
                  </button>
                </div>
              </div>
            </div>
          )}

          {deployment.deployed_at && (
            <p className="text-xs text-slate-400 mt-2">
              Deployed {new Date(deployment.deployed_at).toLocaleString()}
            </p>
          )}
        </div>
      ) : (
        <div className="card p-5 text-center">
          <p className="text-sm text-slate-500 mb-3">No active deployment.</p>
          {versions.length > 0 && (
            <div className="inline-flex flex-col items-center gap-2">
              <button
                onClick={() => onDeploy(versions[0].id)}
                disabled={isActing || !membersReady}
                className="btn-primary text-xs inline-flex items-center gap-1"
                title={!membersReady ? "All member agents must have active production deployments before deploying this workflow." : undefined}
              >
                <Rocket size={12} /> Deploy Latest ({versions[0].version_label})
              </button>
              {!membersReady && (
                <p className="text-xs text-red-500">Deploy all member agents first</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Member Topology Card — workflows only */}
      {isWorkflow && memberTopology.length > 0 && (
        <MemberTopologyCard members={memberTopology} />
      )}

      {/* Production Chat Card — reactive agents only */}
      {deployment && deployment.status === "running" && executionShape === "reactive" && (
        <div className="card p-5 border-blue-200 bg-blue-50/30">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
              <MessageCircle size={14} className="text-blue-600" />
              Production Chat
            </h3>
            <Link
              to={`/catalog/${artifact.id}/chat`}
              className="btn-primary text-xs inline-flex items-center gap-1"
            >
              Open Chat <ExternalLink size={10} />
            </Link>
          </div>
          <div className="mt-2">
            <p className="text-xs text-slate-500 mb-1.5">API endpoint for integrators:</p>
            <div className="flex items-center gap-2">
              <code className="text-xs bg-white border border-slate-200 text-slate-700 px-2 py-1 rounded flex-1 truncate">
                POST {externalBase}/chat
              </code>
              <button
                onClick={() => { navigator.clipboard.writeText(`${externalBase}/chat`); toast.success("Copied"); }}
                className="text-slate-400 hover:text-slate-600 shrink-0"
              >
                <Copy size={12} />
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Internal API Card — collapsible */}
      {deployment && deployment.status === "running" && (
        <InternalApiCard
          endpoints={endpoints}
          executionShape={executionShape}
        />
      )}

      {/* Agent Metadata */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Agent Info</h3>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <p className="text-xs text-slate-400 uppercase mb-0.5">Type</p>
            <p className="font-mono text-slate-700">{artifact.type}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400 uppercase mb-0.5">Team</p>
            <p className="text-slate-700">{artifact.team}</p>
          </div>
          <div>
            <p className="text-xs text-slate-400 uppercase mb-0.5">Execution Shape</p>
            <p className="font-mono text-slate-700">{executionShape}</p>
          </div>
          {artifact.description && (
            <div className="col-span-2">
              <p className="text-xs text-slate-400 uppercase mb-0.5">Description</p>
              <p className="text-slate-700">{artifact.description}</p>
            </div>
          )}
        </div>
      </div>

      {/* Past Deployments (history) */}
      {allDeployments.filter((d) => d.status === "terminated" || d.status === "failed").length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">Past Deployments</h3>
          <div className="space-y-2">
            {allDeployments
              .filter((d) => d.status === "terminated" || d.status === "failed")
              .map((d) => (
                <div key={d.id} className="flex items-center gap-3 text-xs text-slate-500">
                  <span className={`badge ${STATUS_COLORS[d.status] || "bg-slate-100"}`}>{d.status}</span>
                  <span>{d.version_label || "—"}</span>
                  <span>{d.updated_at ? new Date(d.updated_at).toLocaleDateString() : "—"}</span>
                </div>
              ))}
          </div>
        </div>
      )}
    </div>
  );
}

function MemberTopologyCard({ members }: { members: MemberTopologyEntry[] }) {
  const deployedCount = members.filter((m) => m.has_production_deployment).length;
  const allDeployed = deployedCount === members.length;

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-slate-700 flex items-center gap-2">
          <Users size={14} className="text-indigo-500" />
          Member Topology
          <span className="badge bg-slate-100 text-slate-500 text-xs ml-1">{members.length}</span>
        </h3>
      </div>
      <div className="space-y-2">
        {members
          .sort((a, b) => (a.position ?? 999) - (b.position ?? 999))
          .map((m) => (
          <div key={m.agent_id} className="flex items-center justify-between text-sm py-1.5 px-2 rounded hover:bg-slate-50">
            <div className="flex items-center gap-2">
              <span className="text-slate-800 font-medium">{m.agent_name}</span>
              {m.role && (
                <span className="badge bg-slate-100 text-slate-500 text-xs">{m.role}</span>
              )}
              {m.position != null && (
                <span className="text-xs text-slate-400">#{m.position + 1}</span>
              )}
            </div>
            <div className="flex items-center gap-1.5">
              <span
                className={`w-2 h-2 rounded-full ${
                  m.has_production_deployment ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <span className={`text-xs font-medium ${
                m.has_production_deployment ? "text-green-600" : "text-red-600"
              }`}>
                {m.has_production_deployment ? "Deployed" : "Not deployed"}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className={`mt-3 pt-3 border-t border-slate-100 text-xs font-medium ${
        allDeployed ? "text-green-600" : "text-red-600"
      }`}>
        {allDeployed
          ? `${deployedCount}/${members.length} members deployed`
          : `${deployedCount}/${members.length} members deployed — deploy missing members before deploying this workflow`
        }
      </div>
    </div>
  );
}

function StatCard({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="card p-4 flex flex-col gap-1">
      <div className="flex items-center gap-1.5 text-xs text-slate-500">
        {icon}
        {label}
      </div>
      <p className="text-lg font-semibold text-slate-800">{value}</p>
    </div>
  );
}

function EndpointRow({ label, value, method }: { label: string; value: string; method: string }) {
  return (
    <div className="flex items-center gap-3">
      <span className="badge bg-blue-50 text-blue-600 text-[10px] font-mono w-10 text-center">{method}</span>
      <span className="text-xs text-slate-600 w-28">{label}</span>
      <code className="text-xs bg-slate-50 text-slate-700 px-2 py-1 rounded flex-1 truncate">{value}</code>
      <button
        onClick={() => { navigator.clipboard.writeText(value); toast.success("Copied"); }}
        className="text-slate-400 hover:text-slate-600"
      >
        <Copy size={12} />
      </button>
    </div>
  );
}

function InternalApiCard({
  endpoints,
  executionShape,
}: {
  endpoints: { label: string; value: string; method: string }[];
  executionShape: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="card p-4 border-slate-200">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center justify-between w-full text-left"
      >
        <h3 className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-2">
          Internal API (cluster only)
          <span className="badge bg-slate-100 text-slate-400 text-[10px] normal-case">{executionShape}</span>
        </h3>
        {open ? <ChevronDown size={14} className="text-slate-400" /> : <ChevronRight size={14} className="text-slate-400" />}
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          {endpoints.map((ep) => (
            <EndpointRow key={ep.label} label={ep.label} value={ep.value} method={ep.method} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Runs Tab
// ---------------------------------------------------------------------------
const RUN_STATUS_COLORS: Record<string, string> = {
  running: "bg-blue-100 text-blue-700",
  completed: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
  cancelled: "bg-slate-100 text-slate-600",
};

function RunsTab({ runs, isLoading }: { runs: CatalogRun[]; isLoading: boolean }) {
  const [traceId, setTraceId] = useState<string | null>(null);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-slate-400">
        <Loader2 size={16} className="animate-spin mr-2" /> Loading runs…
      </div>
    );
  }

  if (runs.length === 0) {
    return (
      <p className="text-sm text-slate-400 py-8 text-center">
        No production runs yet. Deploy a version and invoke the agent to see runs here.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-[1fr_110px_90px_90px_100px_70px_60px_70px_50px] gap-2 text-xs font-medium text-slate-500 px-3 py-2 border-b border-slate-200">
        <span>Agent</span>
        <span>User</span>
        <span>Status</span>
        <span>Trigger</span>
        <span>Started</span>
        <span>Latency</span>
        <span>Score</span>
        <span>Cost</span>
        <span>Trace</span>
      </div>
      {runs.map((r) => (
        <div
          key={r.id}
          className="grid grid-cols-[1fr_110px_90px_90px_100px_70px_60px_70px_50px] gap-2 items-center text-sm px-3 py-2 rounded hover:bg-slate-50"
        >
          <span className="truncate font-medium text-slate-800">{r.agent_name}</span>
          <span className="text-xs text-slate-500 truncate" title={r.run_by || r.user_id || undefined}>
            {r.run_by || r.user_id || "—"}
          </span>
          <span className={`badge text-xs ${RUN_STATUS_COLORS[r.status] || "bg-slate-100 text-slate-600"}`}>
            {r.status}
          </span>
          <span className="text-xs text-slate-500">{r.trigger_type || "—"}</span>
          <span className="text-xs text-slate-500">
            {new Date(r.started_at).toLocaleString(undefined, {
              month: "short",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
          <span className="text-xs text-slate-500">
            {r.latency_ms != null ? `${(r.latency_ms / 1000).toFixed(1)}s` : "—"}
          </span>
          <span className="text-xs text-slate-500">
            {r.judge_score != null ? r.judge_score.toFixed(2) : "—"}
          </span>
          <span className="text-xs text-slate-500">
            {r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : "—"}
          </span>
          <span>
            {r.langfuse_trace_id ? (
              <button
                onClick={() => setTraceId(r.langfuse_trace_id)}
                className="text-blue-500 hover:text-blue-700"
                title="View trace"
              >
                <Eye size={14} />
              </button>
            ) : (
              <span className="text-slate-300">—</span>
            )}
          </span>
        </div>
      ))}

      {traceId && <TraceDrawer traceId={traceId} onClose={() => setTraceId(null)} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Versions Tab
// ---------------------------------------------------------------------------
function VersionsTab({
  versions,
  onDeploy,
  isDeploying,
  membersReady = true,
}: {
  versions: CatalogVersion[];
  onDeploy: (versionId: string) => void;
  isDeploying: boolean;
  membersReady?: boolean;
}) {
  if (versions.length === 0) {
    return <p className="text-sm text-slate-400 py-8 text-center">No versions promoted yet.</p>;
  }

  return (
    <div className="space-y-3">
      {versions.map((v) => (
        <div key={v.id} className="card flex items-center justify-between">
          <div>
            <span className="font-semibold text-slate-900">{v.version_label}</span>
            <span className="text-xs text-slate-400 ml-3">
              Promoted {new Date(v.promoted_at).toLocaleDateString()}
              {v.promoted_by && ` by ${v.promoted_by}`}
            </span>
            {v.notes && <p className="text-xs text-slate-500 mt-1">{v.notes}</p>}
            {v.config_snapshot && Object.keys(v.config_snapshot).length > 0 && (
              <p className="text-xs text-slate-400 mt-1">
                {v.config_snapshot.model ? `Model: ${String(v.config_snapshot.model)}` : null}
                {v.config_snapshot.execution_shape ? ` · ${String(v.config_snapshot.execution_shape)}` : null}
                {v.config_snapshot.orchestration ? ` · ${String(v.config_snapshot.orchestration)}` : null}
              </p>
            )}
          </div>
          <button
            onClick={() => onDeploy(v.id)}
            disabled={isDeploying || !membersReady}
            className="btn-primary text-xs flex items-center gap-1"
            title={!membersReady ? "All member agents must have active production deployments first." : undefined}
          >
            <Rocket size={12} /> Deploy
          </button>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Settings Tab
// ---------------------------------------------------------------------------
function SettingsTab({
  artifact,
  deployment,
  grantedTeams,
  versions,
}: {
  artifact: { name: string; type: string; team: string };
  deployment: CatalogDeployment | null;
  grantedTeams: string[];
  versions: CatalogVersion[];
}) {
  const currentVersion = versions.find((v) => v.id === deployment?.version_id);
  const config = (currentVersion?.config_snapshot || {}) as Record<string, string | string[] | { name?: string }[]>;

  return (
    <div className="space-y-6">
      {/* Access */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Access Grants</h3>
        {grantedTeams.length === 0 ? (
          <p className="text-sm text-slate-400">No teams granted access yet.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {grantedTeams.map((t) => (
              <span key={t} className="badge bg-green-50 text-green-700 border border-green-200 text-xs">
                {t}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Config */}
      {Object.keys(config).length > 0 && (
        <div className="card p-5">
          <h3 className="text-sm font-semibold text-slate-700 mb-3">
            Deployed Config {currentVersion && <span className="text-slate-400 font-normal">({currentVersion.version_label})</span>}
          </h3>
          <div className="grid grid-cols-2 gap-4 text-sm">
            {config.model && (
              <div>
                <p className="text-xs text-slate-400 uppercase mb-0.5">Model</p>
                <p className="font-mono text-slate-700">{String(config.model)}</p>
              </div>
            )}
            {config.execution_shape && (
              <div>
                <p className="text-xs text-slate-400 uppercase mb-0.5">Execution Shape</p>
                <p className="font-mono text-slate-700">{String(config.execution_shape)}</p>
              </div>
            )}
            {config.orchestration && (
              <div>
                <p className="text-xs text-slate-400 uppercase mb-0.5">Orchestration</p>
                <p className="font-mono text-slate-700">{String(config.orchestration)}</p>
              </div>
            )}
            {config.agent_type && (
              <div>
                <p className="text-xs text-slate-400 uppercase mb-0.5">Agent Type</p>
                <p className="font-mono text-slate-700">{String(config.agent_type)}</p>
              </div>
            )}
          </div>
          {config.tools && Array.isArray(config.tools) && config.tools.length > 0 && (
            <div className="mt-4 pt-3 border-t border-slate-100">
              <p className="text-xs text-slate-400 uppercase mb-2">Tools ({config.tools.length})</p>
              <div className="flex flex-wrap gap-1">
                {(config.tools as Array<{ name?: string }>).map((t, i) => (
                  <span key={i} className="badge bg-slate-100 text-slate-600 text-xs">
                    {t.name || `tool-${i}`}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
