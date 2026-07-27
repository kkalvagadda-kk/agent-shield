// ---------------------------------------------------------------------------
// McpServersPage.tsx — MCP-as-a-tool-source: the Settings list + Register form
// (Phase 12 / T061). Mirrors KnowledgeBasesPage's list-plus-modal shape but the
// modal drives POST /mcp-servers/ (register + synchronous discover).
//
// On a successful register we invalidate ["mcp-servers"] AND navigate to the new
// server's detail page — where the discovered-tools table (FR-MCP-41) is the
// proof the server connected. Registration returns 201 with status="connected"
// OR "error" (unreachable upstream is not an API failure), so we key the toast
// off the returned status, not the HTTP code.
// ---------------------------------------------------------------------------

import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Server, Plus, X, Wrench, Loader2, AlertCircle, CheckCircle2 } from "lucide-react";
import { toast } from "sonner";
import {
  listMcpServers,
  createMcpServer,
  type McpServer,
  type CreateMcpServerPayload,
  type McpIdentityMode,
} from "../api/mcpServersApi";
import { listAuthConfigs } from "../api/registryApi";
import { mcpErrorMessage } from "../lib/mcpError";

const MCP_QUERY_KEY = ["mcp-servers"];

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

export default function McpServersPage() {
  const [showNew, setShowNew] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: MCP_QUERY_KEY,
    queryFn: () => listMcpServers(),
  });

  const servers = useMemo<McpServer[]>(
    () => (Array.isArray(data) ? data : []),
    [data]
  );

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">MCP Servers</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Register upstream <span className="font-mono text-xs bg-slate-100 px-1 rounded">Model Context Protocol</span> servers —
            their tools are discovered and governed like any other tool.
          </p>
        </div>
        <button onClick={() => setShowNew(true)} className="btn-primary">
          <Plus size={14} /> Register Server
        </button>
      </div>

      {isLoading && (
        <div className="card px-4 py-10 text-center text-slate-400">
          <Loader2 size={16} className="inline animate-spin mr-2" /> Loading MCP servers…
        </div>
      )}
      {isError && !isLoading && (
        <div className="card px-4 py-10 text-center text-red-500">Failed to load MCP servers.</div>
      )}
      {!isLoading && !isError && servers.length === 0 && (
        <div className="card px-4 py-10 text-center text-slate-400">
          No MCP servers registered yet. Register one to discover its tools.
        </div>
      )}

      {/* Servers are TILES, not table rows. A server is a thing you browse and
          pick — a handful of them, each with a name, a URL, a health state and a
          tool count — which is exactly what a tile shows better than a row. (The
          discovered-tools tab on the detail page stays a table: that IS a dense
          read-only inventory, and its columns are the point.) The whole tile is
          the link, so the click target is the card rather than the name text. */}
      {!isLoading && !isError && servers.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4" data-testid="mcp-server-tiles">
          {servers.map((s) => (
            <Link
              key={s.id}
              to={`/mcp-servers/${s.id}`}
              className="card p-4 flex flex-col gap-3 hover:border-blue-300 hover:shadow-sm transition-all group"
            >
              <div className="flex items-start gap-2.5">
                <Server size={16} className="text-blue-500 shrink-0 mt-0.5" />
                <div className="min-w-0 flex-1">
                  <p className="font-semibold text-slate-900 group-hover:text-blue-600 truncate">
                    {s.name}
                  </p>
                  <p className="text-xs text-slate-400 font-mono truncate">{s.server_url}</p>
                </div>
                <StatusBadge status={s.status} />
              </div>

              {s.description && (
                <p className="text-xs text-slate-500 leading-relaxed line-clamp-2">{s.description}</p>
              )}

              <div className="flex items-center gap-1.5 flex-wrap text-xs mt-auto pt-1">
                <span className={`badge ${s.is_external ? "bg-indigo-50 text-indigo-700" : "bg-slate-100 text-slate-600"}`}>
                  {s.is_external ? "External" : "Internal"}
                </span>
                <span className="badge bg-slate-100 text-slate-600 inline-flex items-center gap-1">
                  <Wrench size={11} className="text-slate-400" />
                  {s.discovered_tool_count} {s.discovered_tool_count === 1 ? "tool" : "tools"}
                </span>
                {s.owner_team && <span className="badge bg-slate-100 text-slate-600">{s.owner_team}</span>}
              </div>

              {/* Last sync is the freshness signal for the tool count above it —
                  a stale count is worse than no count, because it reads current. */}
              <p className="text-xs text-slate-400 border-t border-slate-100 pt-2">
                {s.last_synced_at
                  ? `Synced ${new Date(s.last_synced_at).toLocaleString()}`
                  : "Never synced"}
              </p>
            </Link>
          ))}
        </div>
      )}

      {showNew && <RegisterServerModal onClose={() => setShowNew(false)} />}
    </div>
  );
}

function RegisterServerModal({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const navigate = useNavigate();

  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [serverUrl, setServerUrl] = useState("");
  const [ownerTeam, setOwnerTeam] = useState("");
  const [isExternal, setIsExternal] = useState(false);
  const [identityMode, setIdentityMode] = useState<McpIdentityMode>("none");
  const [authConfigId, setAuthConfigId] = useState("");
  const [scanResults, setScanResults] = useState(true);
  // WS-2 (Phase 4): an external server can authenticate via a per-user OAuth 2.1
  // grant instead of a stored credential. Only meaningful for external servers.
  const [requiresOAuth, setRequiresOAuth] = useState(false);
  const oauthEnabled = isExternal && requiresOAuth;

  const { data: authConfigsData } = useQuery({
    queryKey: ["auth-configs"],
    queryFn: () => listAuthConfigs(),
  });
  const authConfigs = authConfigsData?.items ?? [];

  const createMutation = useMutation({
    mutationFn: () => {
      const payload: CreateMcpServerPayload = {
        name: name.trim(),
        server_url: serverUrl.trim(),
        transport: "streamable_http",
        is_external: isExternal,
        // An external server MUST be identity_mode "none" (server-enforced) — force
        // it here so the payload is never a rejected combo.
        identity_mode: isExternal ? "none" : identityMode,
        scan_results: scanResults,
        // OAuth (WS-2) is an external-only auth mode; it REPLACES the static
        // credential, so we never send auth_config_id alongside it.
        ...(isExternal ? { external_auth_mode: oauthEnabled ? "oauth" : "static" } : {}),
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(ownerTeam.trim() ? { owner_team: ownerTeam.trim() } : {}),
        ...(authConfigId && !oauthEnabled ? { auth_config_id: authConfigId } : {}),
      };
      return createMcpServer(payload);
    },
    onSuccess: (server) => {
      qc.invalidateQueries({ queryKey: MCP_QUERY_KEY });
      if (server.external_auth_mode === "oauth") {
        // Discovery needs a per-user token, so an OAuth server registers with 0
        // tools until the user authorizes on the detail page (contract §3 / C9).
        toast.success("Registered — authorize on the server page to discover its tools.");
      } else if (server.status === "error") {
        toast.error(
          server.health_detail?.last_error
            ? `Registered, but discovery failed: ${server.health_detail.last_error}`
            : "Registered, but the server could not be reached. Open it to retry."
        );
      } else {
        toast.success(`Registered — discovered ${server.discovered_tool_count} tool(s).`);
      }
      onClose();
      // Land on the detail page: the discovered-tools table is the proof.
      navigate(`/mcp-servers/${server.id}`);
    },
    onError: (err) => toast.error(mcpErrorMessage(err, "Failed to register server.")),
  });

  const canSubmit =
    name.trim().length > 0 && serverUrl.trim().length > 0 && !createMutation.isPending;

  return (
    <div className="fixed inset-0 bg-slate-900/40 flex items-center justify-center z-50 p-4" onClick={onClose}>
      <div className="card max-w-lg w-full relative max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <button onClick={onClose} className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"><X size={16} /></button>
        <h2 className="text-lg font-semibold text-slate-900 mb-5">Register MCP Server</h2>
        <div className="space-y-4">
          <div className="space-y-1">
            <label className="label" htmlFor="mcp-name">Name</label>
            <input
              id="mcp-name"
              className="input font-mono"
              placeholder="github-mcp"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
            <p className="text-xs text-slate-400">Immutable after creation — every discovered tool is namespaced <code className="font-mono bg-slate-100 px-1 rounded">{"{name}__{tool}"}</code>.</p>
          </div>

          <div className="space-y-1">
            <label className="label" htmlFor="mcp-url">Server URL</label>
            <input
              id="mcp-url"
              className="input font-mono"
              placeholder="https://mcp.example.com/mcp"
              value={serverUrl}
              onChange={(e) => setServerUrl(e.target.value)}
            />
          </div>

          <div className="space-y-1">
            <label className="label" htmlFor="mcp-desc">Description</label>
            <input
              id="mcp-desc"
              className="input"
              placeholder="What this server provides"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>

          {/* Transport — streamable_http only in Phase 1; stdio is grayed out to
              agree with the API's 422 rejection. */}
          <div className="space-y-1">
            <label className="label">Transport</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input type="radio" checked readOnly className="accent-blue-600" />
                <span className="text-sm font-medium text-slate-700">streamable_http</span>
              </label>
              <label className="flex items-center gap-2 cursor-not-allowed opacity-50" title="Available in Phase 3">
                <input type="radio" disabled className="accent-blue-600" />
                <span className="text-sm font-medium text-slate-500">stdio</span>
                <span className="badge bg-slate-100 text-slate-400 text-xs">Phase 3</span>
              </label>
            </div>
          </div>

          {/* Scope toggle */}
          <div className="space-y-1">
            <label className="label">Scope</label>
            <div className="flex gap-4">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="mcp-scope"
                  checked={!isExternal}
                  onChange={() => setIsExternal(false)}
                  className="accent-blue-600"
                />
                <span className="text-sm font-medium text-slate-700">Internal</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="radio"
                  name="mcp-scope"
                  checked={isExternal}
                  onChange={() => setIsExternal(true)}
                  className="accent-blue-600"
                />
                <span className="text-sm font-medium text-slate-700">External</span>
              </label>
            </div>
          </div>

          {/* Internal → identity mode. External → identity is always "none". */}
          {!isExternal ? (
            <div className="space-y-1">
              <label className="label" htmlFor="mcp-identity">Identity mode</label>
              <select
                id="mcp-identity"
                className="input"
                value={identityMode}
                onChange={(e) => setIdentityMode(e.target.value as McpIdentityMode)}
                aria-label="Identity mode"
              >
                <option value="none">None</option>
                <option value="on_behalf_of">On behalf of caller</option>
                <option value="service_identity">Service identity</option>
              </select>
            </div>
          ) : (
            <p className="text-xs text-slate-400">
              External servers always use identity mode <code className="font-mono bg-slate-100 px-1 rounded">none</code>.
            </p>
          )}

          {/* WS-2 (Phase 4): OAuth toggle — external only. When on, the server
              authenticates via a per-user OAuth 2.1 grant, so the static
              credential picker below is hidden (OAuth replaces it). */}
          {isExternal && (
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={requiresOAuth}
                onChange={(e) => setRequiresOAuth(e.target.checked)}
                className="mt-0.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                aria-label="Server requires OAuth 2.1 authorization"
              />
              <span className="text-sm text-slate-700">
                Server requires OAuth 2.1 authorization
                <span className="block text-xs text-slate-400">
                  Each user authorizes on the server page after registering — no shared credential.
                </span>
              </span>
            </label>
          )}

          {!oauthEnabled && (
            <div className="space-y-1">
              <label className="label" htmlFor="mcp-auth">Credential</label>
              <select
                id="mcp-auth"
                className="input"
                value={authConfigId}
                onChange={(e) => setAuthConfigId(e.target.value)}
                aria-label="Credential"
              >
                <option value="">None</option>
                {authConfigs.map((ac) => (
                  <option key={ac.id} value={ac.id}>{ac.name} ({ac.type})</option>
                ))}
              </select>
            </div>
          )}

          <div className="space-y-1">
            <label className="label" htmlFor="mcp-team">Team</label>
            <input
              id="mcp-team"
              className="input"
              placeholder="platform"
              value={ownerTeam}
              onChange={(e) => setOwnerTeam(e.target.value)}
            />
          </div>

          <label className="flex items-start gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={scanResults}
              onChange={(e) => setScanResults(e.target.checked)}
              className="mt-0.5 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
            />
            <span className="text-sm text-slate-700">
              Scan tool results for safety
              {isExternal && <span className="text-xs text-slate-400 ml-1">(ignored — external results are always scanned)</span>}
            </span>
          </label>
        </div>

        <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-slate-100">
          <button onClick={onClose} className="btn-secondary" disabled={createMutation.isPending}>Cancel</button>
          <button onClick={() => createMutation.mutate()} className="btn-primary" disabled={!canSubmit}>
            {createMutation.isPending ? <Loader2 size={14} className="animate-spin" /> : "Register"}
          </button>
        </div>
      </div>
    </div>
  );
}
