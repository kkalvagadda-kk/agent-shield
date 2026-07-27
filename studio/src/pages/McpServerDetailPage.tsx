// ---------------------------------------------------------------------------
// McpServerDetailPage.tsx — MCP server detail (Phase 12 / T062).
//
//   * Discovered Tools tab → the FR-MCP-41 proof table. Every child Tool of this
//     server, `inactive` ones greyed + struck (never hard-deleted, FR-MCP-04).
//   * Settings tab → PUT editable fields (description / credential / team /
//     identity mode / scan_results), Sync (re-run discovery), and a guarded
//     Delete that surfaces the 409 blocking-agents message.
//
// When status="error" a red banner sits above the tabs with a Sync/Retry — a
// failed register/sync is not an API error, it's an unhealthy server row.
// ---------------------------------------------------------------------------

import { Fragment, useEffect, useMemo, useState } from "react";
import { Link, useParams, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Server, Wrench, Trash2, RotateCw, Loader2, AlertCircle, ExternalLink,
  Activity, CheckCircle2, KeyRound, Search, Unlink,
} from "lucide-react";
import { toast } from "sonner";
import {
  getMcpServer, updateMcpServer, syncMcpServer, deleteMcpServer,
  startMcpOAuth, getMcpOAuthStatus, disconnectMcpOAuth,
  type McpServerTool, type McpIdentityMode,
} from "../api/mcpServersApi";
import { listAuthConfigs } from "../api/registryApi";
import { mcpErrorMessage } from "../lib/mcpError";
import { PiiChip, RiskChip } from "../components/shared/ToolChips";

type Tab = "tools" | "settings";

// Risk / PII chips come from components/shared/ToolChips — this page used to
// keep its own RISK_CLS map, which is how the same risk level ended up a
// different colour here than on the picker tile.

// Duplicated from McpServersPage's list pill (contracts/studio-mcp-servers-phase2
// §1 permits the tiny map to be duplicated) so the Health panel's status matches
// the list's visual language exactly.
function StatusBadge({ status }: { status: string }) {
  if (status === "connected")
    return (
      <span className="badge inline-flex items-center gap-1 bg-green-100 text-green-700">
        <CheckCircle2 size={12} /> Connected
      </span>
    );
  if (status === "error")
    return (
      <span className="badge inline-flex items-center gap-1 bg-red-100 text-red-700">
        <AlertCircle size={12} /> Error
      </span>
    );
  return <span className="badge bg-slate-100 text-slate-500">Disconnected</span>;
}

const fmtTs = (ts?: string | null): string =>
  ts ? new Date(ts).toLocaleString() : "—";

export default function McpServerDetailPage() {
  const { id } = useParams();
  const serverId = id ?? "";
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [tab, setTab] = useState<Tab>("tools");

  const { data: server, isLoading } = useQuery({
    queryKey: ["mcp-server", serverId],
    queryFn: () => getMcpServer(serverId),
    enabled: !!serverId,
    // Poll so the periodic health loop (WS-A) surfaces status/health_detail
    // changes without a manual reload (FR-MCP-22 "surface in Studio").
    refetchInterval: 15000,
  });

  // OAuth callback landing (WS-2): the server-side callback 302s back here with
  // ?oauth=connected|denied|invalid_state|error. Toast the outcome, invalidate the
  // grant + server queries so the panel reflects the new grant, then STRIP the
  // param (replace) so a reload doesn't re-toast.
  const oauthResult = searchParams.get("oauth");
  useEffect(() => {
    if (!oauthResult || !serverId) return;
    if (oauthResult === "connected") {
      toast.success("Connected.");
    } else {
      const messages: Record<string, string> = {
        denied: "Authorization was denied.",
        invalid_state:
          "Authorization could not be verified (expired or tampered request). Please try again.",
        error: "Authorization failed. Please try again.",
      };
      toast.error(messages[oauthResult] ?? "Authorization failed.");
    }
    qc.invalidateQueries({ queryKey: ["mcp-oauth-status", serverId] });
    qc.invalidateQueries({ queryKey: ["mcp-server", serverId] });
    navigate(`/mcp-servers/${serverId}`, { replace: true });
  }, [oauthResult, serverId, qc, navigate]);

  const syncMutation = useMutation({
    mutationFn: () => syncMcpServer(serverId),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["mcp-server", serverId] });
      qc.invalidateQueries({ queryKey: ["mcp-servers"] });
      if (res.server.status === "error") {
        toast.error(
          res.server.health_detail?.last_error
            ? `Sync failed: ${res.server.health_detail.last_error}`
            : "Sync failed — the server could not be reached."
        );
      } else {
        toast.success(
          `Synced — ${res.tools_added} added, ${res.tools_updated} updated, ${res.tools_inactivated} inactivated.`
        );
      }
    },
    onError: (err) => toast.error(mcpErrorMessage(err, "Sync failed.")),
  });

  const tools = useMemo<McpServerTool[]>(() => server?.tools ?? [], [server]);

  if (isLoading) {
    return (
      <div className="max-w-5xl mx-auto px-6 py-8 text-slate-400">
        <Loader2 size={16} className="inline animate-spin mr-2" /> Loading server…
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <Link to="/mcp-servers" className="text-sm text-slate-500 hover:text-slate-800 inline-flex items-center gap-1 mb-4">
        <ArrowLeft size={14} /> MCP Servers
      </Link>

      <div className="flex items-start justify-between mb-1">
        <h1 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
          <Server size={20} className="text-blue-500" /> {server?.name ?? "MCP Server"}
        </h1>
        <div className="flex items-center gap-2">
          <span className={`badge ${server?.is_external ? "bg-indigo-50 text-indigo-700" : "bg-slate-100 text-slate-600"}`}>
            {server?.is_external ? "External" : "Internal"}
          </span>
          {server?.owner_team && <span className="badge bg-slate-100 text-slate-500">{server.owner_team}</span>}
        </div>
      </div>
      <p className="text-sm text-slate-500 mb-1 font-mono">{server?.server_url}</p>
      {server?.description && <p className="text-sm text-slate-500 mb-4">{server.description}</p>}

      {/* Error banner — a failed register/sync surfaces here, not as an HTTP error. */}
      {server?.status === "error" && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 mb-5 flex items-start gap-3">
          <AlertCircle size={18} className="text-red-500 shrink-0 mt-0.5" />
          <div className="flex-1">
            <p className="text-sm font-medium text-red-800">This server is unreachable.</p>
            {server.health_detail?.last_error && (
              <p className="text-xs text-red-600 mt-0.5 font-mono">{server.health_detail.last_error}</p>
            )}
          </div>
          <button
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
            className="btn-secondary shrink-0"
          >
            {syncMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : <><RotateCw size={14} /> Retry</>}
          </button>
        </div>
      )}

      {/* OAuth Connection — WS-2 (Phase 4): shown ONLY for an external server whose
          external_auth_mode is "oauth". A static/internal server's detail page is
          unchanged. The frontend only sees the consent URL + the status enum — never
          a token. */}
      {server?.is_external && server?.external_auth_mode === "oauth" && (
        <OAuthConnectionPanel serverId={serverId} />
      )}

      {/* Health — WS-A: live reachability + discovery + identity surface (Phase 2).
          Reads the already-fetched `server` (polled every 15s); no extra API call. */}
      <div className="card mb-6">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-slate-700 inline-flex items-center gap-1.5">
            <Activity size={14} className="text-slate-400" /> Health
          </h2>
          <StatusBadge status={server?.status ?? "disconnected"} />
        </div>
        <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
          <div className="flex items-center justify-between gap-4">
            <dt className="text-slate-500">Last successful check</dt>
            <dd className="text-slate-700">{fmtTs(server?.health_detail?.last_success_at)}</dd>
          </div>
          {(server?.health_detail?.consecutive_failures ?? 0) > 0 && (
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Consecutive failures</dt>
              <dd className="text-red-600 font-medium">{server?.health_detail?.consecutive_failures}</dd>
            </div>
          )}
          <div className="flex items-center justify-between gap-4">
            <dt className="text-slate-500">Last discovery</dt>
            <dd className="text-slate-700">{fmtTs(server?.last_synced_at)}</dd>
          </div>
          <div className="flex items-center justify-between gap-4">
            <dt className="text-slate-500">Change notifications</dt>
            <dd className="text-slate-700">{server?.list_changed_supported ? "subscribed" : "not supported"}</dd>
          </div>
          <div className="flex items-center justify-between gap-4">
            <dt className="text-slate-500">Identity</dt>
            <dd className="text-slate-700">
              {server?.identity_mode}
              {server?.identity_mode === "on_behalf_of" && (
                <span className="text-xs text-amber-600 ml-1">(pending — Decision 29)</span>
              )}
            </dd>
          </div>
        </dl>
        {/* The health-loop's last failure reason — shown only for an unhealthy server. */}
        {server?.status === "error" && server?.health_detail?.last_error && (
          <p className="text-xs text-red-600 font-mono mt-3 pt-2 border-t border-red-100">
            {server.health_detail.last_error}
          </p>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-6 border-b border-slate-200 mb-6">
        {([["tools", `Discovered Tools · ${tools.length}`], ["settings", "Settings"]] as [Tab, string][]).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`pb-2.5 text-sm font-medium border-b-2 -mb-px transition-colors ${tab === t ? "border-blue-500 text-blue-600" : "border-transparent text-slate-500 hover:text-slate-800"}`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "tools" && (
        <ToolsTab tools={tools} onSync={() => syncMutation.mutate()} syncing={syncMutation.isPending} />
      )}
      {tab === "settings" && <SettingsTab serverId={serverId} />}
    </div>
  );
}

// OAuth Connection panel (WS-2 / Phase 4). Drives off GET …/oauth/status (its own
// query, polled like Health). Authorize does a full-page redirect to the upstream
// consent URL — the frontend NEVER completes the flow or touches a token itself.
function OAuthConnectionPanel({ serverId }: { serverId: string }) {
  const qc = useQueryClient();

  const { data: oauth, isLoading } = useQuery({
    queryKey: ["mcp-oauth-status", serverId],
    queryFn: () => getMcpOAuthStatus(serverId),
    enabled: !!serverId,
    refetchInterval: 15000,
  });

  const authorizeMutation = useMutation({
    mutationFn: () => startMcpOAuth(serverId),
    onSuccess: (data) => {
      // Full-page redirect to the AS's consent screen. The server-side callback
      // finishes the exchange and 302s back to ?oauth=connected|error.
      window.location.href = data.authorization_url;
    },
    onError: (err) => toast.error(mcpErrorMessage(err, "Could not start authorization.")),
  });

  const disconnectMutation = useMutation({
    mutationFn: () => disconnectMcpOAuth(serverId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp-oauth-status", serverId] });
      qc.invalidateQueries({ queryKey: ["mcp-server", serverId] });
      toast.success("Disconnected.");
    },
    onError: (err) => toast.error(mcpErrorMessage(err, "Disconnect failed.")),
  });

  const authorized = oauth?.status === "authorized";
  const errored = oauth?.status === "error";

  return (
    <div className="card mb-6">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-slate-700 inline-flex items-center gap-1.5">
          <KeyRound size={14} className="text-slate-400" /> OAuth Connection
        </h2>
        {authorized ? (
          <span className="badge inline-flex items-center gap-1 bg-green-100 text-green-700">
            <CheckCircle2 size={12} /> Connected
          </span>
        ) : (
          <span className="badge inline-flex items-center gap-1 bg-amber-100 text-amber-700">
            <AlertCircle size={12} /> Needs authorization
          </span>
        )}
      </div>

      {isLoading ? (
        <p className="text-sm text-slate-400">
          <Loader2 size={14} className="inline animate-spin mr-1.5" /> Checking authorization…
        </p>
      ) : authorized ? (
        <div className="space-y-3">
          <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2 text-sm">
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Scopes</dt>
              <dd
                className="text-slate-700 font-mono text-xs truncate max-w-[16rem]"
                title={oauth?.scopes ?? undefined}
              >
                {oauth?.scopes || "—"}
              </dd>
            </div>
            <div className="flex items-center justify-between gap-4">
              <dt className="text-slate-500">Expires</dt>
              <dd className="text-slate-700">{fmtTs(oauth?.token_expires_at)}</dd>
            </div>
          </dl>
          <div className="pt-3 border-t border-slate-100">
            <button
              onClick={() => disconnectMutation.mutate()}
              disabled={disconnectMutation.isPending}
              className="inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-800 disabled:opacity-40"
            >
              {disconnectMutation.isPending
                ? <Loader2 size={14} className="animate-spin" />
                : <><Unlink size={14} /> Disconnect</>}
            </button>
          </div>
        </div>
      ) : (
        <div className="space-y-3">
          <p className="text-sm text-slate-500">
            {errored
              ? "Authorization failed — reconnect to grant access to this server's tools."
              : "This server requires OAuth authorization. Connect your account to discover and call its tools."}
          </p>
          {errored && oauth?.last_error && (
            <p className="text-xs text-red-600 font-mono">{oauth.last_error}</p>
          )}
          <button
            onClick={() => authorizeMutation.mutate()}
            disabled={authorizeMutation.isPending}
            className="btn-primary"
          >
            {authorizeMutation.isPending
              ? <Loader2 size={14} className="animate-spin" />
              : <><KeyRound size={14} /> {errored ? "Re-authorize" : "Authorize"}</>}
          </button>
        </div>
      )}
    </div>
  );
}

function ToolsTab({ tools, onSync, syncing }: { tools: McpServerTool[]; onSync: () => void; syncing: boolean }) {
  const [q, setQ] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);

  // Deliberately still a TABLE, not the tile grid the agent builder uses. Tiles
  // are a selection affordance; nothing here is selectable — binding happens in
  // the agent builder, as the hint below says. The five columns are what you
  // actually scan on this screen, and a tile would drop them.
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? tools.filter(
        (t) =>
          (t.display_name || t.name).toLowerCase().includes(needle) ||
          (t.mcp_tool_name ?? "").toLowerCase().includes(needle) ||
          (t.description ?? "").toLowerCase().includes(needle),
      )
    : tools;

  return (
    <div>
      <div className="flex items-center justify-between mb-3 gap-3">
        <p className="text-xs text-slate-500">
          Tools discovered from this server. Bind them to agents from the agent builder&apos;s Tools picker.
        </p>
        <div className="flex items-center gap-2 shrink-0">
          {/* One server can advertise dozens of tools; an unsearchable table of
              that length is not readable. */}
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              className="input pl-8 py-1.5 text-sm w-56"
              placeholder="Filter tools"
              aria-label="Filter tools"
            />
          </div>
          <button onClick={onSync} disabled={syncing} className="btn-secondary">
            {syncing ? <Loader2 size={14} className="animate-spin" /> : <><RotateCw size={14} /> Sync</>}
          </button>
        </div>
      </div>
      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              {["Tool", "MCP name", "Risk", "PII", "Status", ""].map((h, i) => (
                <th key={h || `sp${i}`} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {tools.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No tools discovered. Sync to re-run discovery.
                </td>
              </tr>
            )}
            {tools.length > 0 && shown.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                  No tool matches &ldquo;{q}&rdquo;.
                </td>
              </tr>
            )}
            {shown.map((t) => {
              const inactive = t.status !== "active";
              const open = expanded === t.id;
              return (
                <Fragment key={t.id}>
                  <tr className={`transition-colors ${inactive ? "bg-slate-50/60" : "hover:bg-slate-50"}`}>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-1.5">
                        <Wrench size={13} className="text-slate-400 shrink-0" />
                        <span className={`font-medium ${inactive ? "text-slate-400 line-through" : "text-slate-900"}`}>
                          {t.display_name || t.name}
                        </span>
                      </div>
                      {t.description && <p className="text-xs text-slate-400 truncate max-w-md mt-0.5 pl-5">{t.description}</p>}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs text-slate-500">{t.mcp_tool_name ?? "—"}</td>
                    <td className="px-4 py-3">
                      <RiskChip risk={t.risk_level} />
                    </td>
                    <td className="px-4 py-3">
                      {t.pii_deanonymize_allowed ? <PiiChip allowed /> : <span className="text-xs text-slate-400">—</span>}
                    </td>
                    <td className="px-4 py-3">
                      {inactive
                        ? <span className="badge bg-slate-100 text-slate-400">inactive</span>
                        : <span className="badge bg-green-100 text-green-700">active</span>}
                    </td>
                    <td className="px-4 py-3 text-right">
                      {/* input_schema is fetched for every discovered tool and,
                          until now, read by nobody. These are the parameters the
                          agent's LLM will be asked to fill in — the one thing you
                          cannot see anywhere else in the product. */}
                      {t.input_schema ? (
                        <button
                          type="button"
                          onClick={() => setExpanded(open ? null : t.id)}
                          aria-expanded={open}
                          className="text-xs text-indigo-600 hover:text-indigo-800"
                        >
                          {open ? "Hide parameters" : "Parameters"}
                        </button>
                      ) : (
                        <span className="text-xs text-slate-300">no schema</span>
                      )}
                    </td>
                  </tr>
                  {open && t.input_schema && (
                    <tr className="bg-slate-50/80">
                      <td colSpan={6} className="px-4 py-3">
                        <pre className="text-xs text-slate-600 overflow-x-auto whitespace-pre-wrap">
                          {JSON.stringify(t.input_schema, null, 2)}
                        </pre>
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function SettingsTab({ serverId }: { serverId: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const { data: server } = useQuery({
    queryKey: ["mcp-server", serverId],
    queryFn: () => getMcpServer(serverId),
    enabled: !!serverId,
  });

  const { data: authConfigsData } = useQuery({
    queryKey: ["auth-configs"],
    queryFn: () => listAuthConfigs(),
  });
  const authConfigs = authConfigsData?.items ?? [];

  const [description, setDescription] = useState<string | null>(null);
  const [ownerTeam, setOwnerTeam] = useState<string | null>(null);
  const [identityMode, setIdentityMode] = useState<string | null>(null);
  const [authConfigId, setAuthConfigId] = useState<string | null>(null);
  const [scanResults, setScanResults] = useState<boolean | null>(null);

  const descVal = description ?? server?.description ?? "";
  const teamVal = ownerTeam ?? server?.owner_team ?? "";
  const identityVal = identityMode ?? server?.identity_mode ?? "none";
  const authVal = authConfigId ?? server?.auth_config_id ?? "";
  const scanVal = scanResults ?? server?.scan_results ?? true;

  const saveMutation = useMutation({
    mutationFn: () =>
      updateMcpServer(serverId, {
        description: descVal,
        owner_team: teamVal,
        // Identity mode is an internal-server concept; never send it for external.
        ...(server?.is_external ? {} : { identity_mode: identityVal as McpIdentityMode }),
        auth_config_id: authVal || null,
        scan_results: scanVal,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp-server", serverId] });
      qc.invalidateQueries({ queryKey: ["mcp-servers"] });
      toast.success("Saved.");
    },
    onError: (err) => toast.error(mcpErrorMessage(err, "Save failed.")),
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteMcpServer(serverId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["mcp-servers"] });
      toast.success("MCP server deleted.");
      navigate("/mcp-servers");
    },
    // The 409 detail is an OBJECT ({message, blocking_agents}) — mcpErrorMessage
    // renders the bound agents so the operator knows what to unbind.
    onError: (err) => toast.error(mcpErrorMessage(err, "Delete failed.")),
  });

  return (
    <div className="max-w-lg space-y-6">
      <div className="space-y-4">
        <div className="space-y-1">
          <label className="label">Name</label>
          <input className="input bg-slate-50 text-slate-500 font-mono" value={server?.name ?? ""} readOnly />
          <p className="text-xs text-slate-400">Immutable — a rename would orphan every discovered tool.</p>
        </div>
        <div className="space-y-1">
          <label className="label">Server URL</label>
          <input className="input bg-slate-50 text-slate-500 font-mono" value={server?.server_url ?? ""} readOnly />
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="mcp-set-desc">Description</label>
          <input id="mcp-set-desc" className="input" value={descVal} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div className="space-y-1">
          <label className="label" htmlFor="mcp-set-team">Team</label>
          <input id="mcp-set-team" className="input" value={teamVal} onChange={(e) => setOwnerTeam(e.target.value)} />
        </div>
        {!server?.is_external && (
          <div className="space-y-1">
            <label className="label" htmlFor="mcp-set-identity">Identity mode</label>
            <select id="mcp-set-identity" className="input" value={identityVal} onChange={(e) => setIdentityMode(e.target.value)} aria-label="Identity mode">
              <option value="none">None</option>
              <option value="on_behalf_of">On behalf of caller</option>
              <option value="service_identity">Service identity</option>
            </select>
          </div>
        )}
        <div className="space-y-1">
          <label className="label" htmlFor="mcp-set-auth">Credential</label>
          <select id="mcp-set-auth" className="input" value={authVal} onChange={(e) => setAuthConfigId(e.target.value)} aria-label="Credential">
            <option value="">None</option>
            {authConfigs.map((ac) => (
              <option key={ac.id} value={ac.id}>{ac.name} ({ac.type})</option>
            ))}
          </select>
        </div>
        <label className="flex items-start gap-2 cursor-pointer">
          <input
            type="checkbox"
            checked={scanVal}
            onChange={(e) => setScanResults(e.target.checked)}
            className="mt-0.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          <span className="text-sm text-slate-700">
            Scan tool results for safety
            {server?.is_external && <span className="text-xs text-slate-400 ml-1">(ignored — external results are always scanned)</span>}
          </span>
        </label>

        <div className="flex justify-between pt-4 border-t border-slate-100">
          <button
            onClick={() => deleteMutation.mutate()}
            disabled={deleteMutation.isPending}
            className="inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-800 disabled:opacity-40"
          ><Trash2 size={14} /> Delete Server</button>
          <button onClick={() => saveMutation.mutate()} disabled={saveMutation.isPending} className="btn-primary">
            {saveMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : "Save"}
          </button>
        </div>
      </div>

      <p className="text-xs text-slate-400 inline-flex items-center gap-1">
        <ExternalLink size={12} /> Deleting is blocked while any discovered tool is bound to an agent.
      </p>
    </div>
  );
}
