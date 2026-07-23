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

      <div className="card p-0 overflow-hidden">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-slate-100 bg-slate-50">
              {["Name", "Scope", "Team", "Tools", "Status", "Synced"].map((h) => (
                <th key={h} className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {isLoading && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-400">
                  <Loader2 size={16} className="inline animate-spin mr-2" /> Loading MCP servers…
                </td>
              </tr>
            )}
            {isError && !isLoading && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-red-500">
                  Failed to load MCP servers.
                </td>
              </tr>
            )}
            {!isLoading && !isError && servers.length === 0 && (
              <tr>
                <td colSpan={6} className="px-4 py-10 text-center text-slate-400">
                  No MCP servers registered yet. Register one to discover its tools.
                </td>
              </tr>
            )}
            {servers.map((s) => (
              <tr key={s.id} className="hover:bg-slate-50 transition-colors">
                <td className="px-4 py-3">
                  <Link to={`/mcp-servers/${s.id}`} className="flex items-center gap-2 group">
                    <Server size={14} className="text-blue-500 shrink-0" />
                    <div>
                      <p className="font-semibold text-slate-900 group-hover:text-blue-600">{s.name}</p>
                      <p className="text-xs text-slate-400 truncate max-w-md font-mono">{s.server_url}</p>
                    </div>
                  </Link>
                </td>
                <td className="px-4 py-3">
                  <span className={`badge ${s.is_external ? "bg-indigo-50 text-indigo-700" : "bg-slate-100 text-slate-600"}`}>
                    {s.is_external ? "External" : "Internal"}
                  </span>
                </td>
                <td className="px-4 py-3 text-slate-600">{s.owner_team ?? "—"}</td>
                <td className="px-4 py-3 text-slate-600">
                  <span className="inline-flex items-center gap-1"><Wrench size={12} className="text-slate-400" />{s.discovered_tool_count}</span>
                </td>
                <td className="px-4 py-3"><StatusBadge status={s.status} /></td>
                <td className="px-4 py-3 text-slate-500 text-xs">
                  {s.last_synced_at ? new Date(s.last_synced_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

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
        ...(description.trim() ? { description: description.trim() } : {}),
        ...(ownerTeam.trim() ? { owner_team: ownerTeam.trim() } : {}),
        ...(authConfigId ? { auth_config_id: authConfigId } : {}),
      };
      return createMcpServer(payload);
    },
    onSuccess: (server) => {
      qc.invalidateQueries({ queryKey: MCP_QUERY_KEY });
      if (server.status === "error") {
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
