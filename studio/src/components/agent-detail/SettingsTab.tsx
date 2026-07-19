import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { KeyRound, Copy, Check, Plus, Trash2 } from "lucide-react";
import {
  listTriggers, updateTrigger, rotateToken, createTrigger, updateAgent,
  createTriggerClient, listTriggerClients, setClientEnabled, deleteTriggerClient,
} from "../../api/registryApi";

interface Props {
  agentName: string;
  memoryEnabled?: boolean;
}

const COMMON_TZ = ["UTC", "America/New_York", "America/Chicago", "America/Los_Angeles", "Europe/London", "Asia/Kolkata"];
const FILTER_OPS = ["eq", "neq", "contains", "gt", "gte", "lt", "lte", "exists", "in"];
// WS-2 T014 — reviewer roles a daemon (scheduled) trigger-run's approval can route to.
// "" = platform default (backend derives "agent:reviewer"). Only meaningful for
// scheduled/daemon triggers (they run under the service identity, no interactive caller).
const APPROVER_ROLES = ["agent:reviewer", "team:reviewer", "platform_admin"];
interface FilterRow { field: string; op: string; value: string; }

export default function SettingsTab({ agentName, memoryEnabled }: Props) {
  const qc = useQueryClient();
  const { data: triggers = [] } = useQuery({
    queryKey: ["triggers", agentName],
    queryFn: () => listTriggers(agentName),
  });

  const [addSchedule, setAddSchedule] = useState(false);
  const [addWebhook, setAddWebhook] = useState(false);

  const schedules = triggers.filter((t) => t.trigger_type === "schedule");
  const webhooks = triggers.filter((t) => t.trigger_type === "webhook");
  const invalidate = () => qc.invalidateQueries({ queryKey: ["triggers", agentName] });

  const memoryMut = useMutation({
    mutationFn: (enabled: boolean) => updateAgent(agentName, { memory_enabled: enabled }),
    onSuccess: () => {
      toast.success("Memory setting updated");
      qc.invalidateQueries({ queryKey: ["agent", agentName] });
    },
    onError: () => toast.error("Failed to update memory setting"),
  });

  return (
    <div className="space-y-4">
      {/* Memory */}
      <div className="card p-5">
        <h3 className="text-sm font-semibold text-slate-700 mb-3">Memory</h3>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={!!memoryEnabled}
            disabled={memoryMut.isPending}
            onChange={(e) => memoryMut.mutate(e.target.checked)}
            className="rounded"
          />
          Enable memory (conversation history + facts across runs)
        </label>
      </div>

      <div className="card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700">Schedule Triggers</h3>
          <button onClick={() => setAddSchedule((v) => !v)} className="btn-secondary text-xs py-1">
            <Plus size={12} /> New schedule trigger
          </button>
        </div>
        {addSchedule && (
          <NewScheduleForm
            agentName={agentName}
            onDone={() => { setAddSchedule(false); invalidate(); }}
          />
        )}
        {schedules.length === 0 && !addSchedule ? (
          <p className="text-sm text-slate-500">No schedule triggers configured for this agent.</p>
        ) : (
          <div className="space-y-4">
            {schedules.map((t) => (
              <TriggerRow key={t.id} agentName={agentName} trigger={t} onSaved={invalidate} />
            ))}
          </div>
        )}
      </div>

      <div className="card p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="text-sm font-semibold text-slate-700">Webhook Triggers</h3>
          <button onClick={() => setAddWebhook((v) => !v)} className="btn-secondary text-xs py-1">
            <Plus size={12} /> New webhook trigger
          </button>
        </div>
        {addWebhook && (
          <NewWebhookForm
            agentName={agentName}
            onDone={() => { setAddWebhook(false); invalidate(); }}
          />
        )}
        {webhooks.length === 0 && !addWebhook ? (
          <p className="text-sm text-slate-500">No webhook triggers configured for this agent.</p>
        ) : (
          <div className="space-y-4">
            {webhooks.map((t) => (
              <WebhookRow key={t.id} agentName={agentName} trigger={t} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function NewScheduleForm({ agentName, onDone }: { agentName: string; onDone: () => void }) {
  const [cron, setCron] = useState("0 9 * * 1");
  const [tz, setTz] = useState("UTC");
  const [alertEmail, setAlertEmail] = useState("");
  const [approverRole, setApproverRole] = useState("agent:reviewer");
  const [payload, setPayload] = useState("");
  const payloadError = payload.trim() ? (() => { try { JSON.parse(payload); return null; } catch (e) { return e instanceof Error ? e.message : "parse error"; } })() : null;
  const create = useMutation({
    mutationFn: () =>
      createTrigger(agentName, {
        trigger_type: "schedule",
        cron_expression: cron,
        timezone: tz,
        alert_email: alertEmail.trim() || null,
        approver_role: approverRole || null,
        ...(payload.trim() ? { input_payload: JSON.parse(payload) } : {}),
      }),
    onSuccess: () => { toast.success("Schedule trigger created"); onDone(); },
    onError: () => toast.error("Failed to create schedule trigger"),
  });
  return (
    <div className="border border-slate-200 rounded-lg p-4 mb-4 space-y-3 bg-slate-50/50">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Cron expression</span>
          <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 9 * * 1"
            className="mt-1 w-full font-mono text-sm border border-slate-300 rounded px-2 py-1.5" />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Timezone</span>
          <select value={tz} onChange={(e) => setTz(e.target.value)}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5">
            {COMMON_TZ.map((z) => <option key={z} value={z}>{z}</option>)}
          </select>
        </label>
      </div>
      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Failure alert email (optional)</span>
        <input type="email" value={alertEmail} onChange={(e) => setAlertEmail(e.target.value)}
          placeholder="oncall@example.com"
          className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5" />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Approver role — who reviews this daemon run&apos;s approvals</span>
        <select value={approverRole} onChange={(e) => setApproverRole(e.target.value)}
          className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5">
          {APPROVER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
        </select>
        <p className="text-xs text-slate-400 mt-0.5">Scheduled runs act under the agent&apos;s service identity — their approvals route to this reviewer role in the Approvals Inbox.</p>
      </label>
      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Input payload — JSON job spec (optional)</span>
        <textarea value={payload} onChange={(e) => setPayload(e.target.value)}
          rows={3}
          placeholder={'{ "task": "weekly-report", "recipients": ["oncall@acme.com"] }'}
          className="mt-1 w-full font-mono text-xs border border-slate-300 rounded px-2 py-1.5 resize-none" />
        {payloadError
          ? <p className="text-xs text-red-600 mt-0.5">Invalid JSON: {payloadError}</p>
          : <p className="text-xs text-slate-400 mt-0.5">Fed to the agent as its input on each fire — add multiple schedules with different payloads to reuse one agent.</p>}
      </label>
      <div className="flex justify-end gap-2">
        <button onClick={onDone} className="btn-secondary text-xs py-1.5">Cancel</button>
        <button onClick={() => create.mutate()} disabled={create.isPending || !!payloadError} className="btn-primary text-xs py-1.5">
          {create.isPending ? "Creating…" : "Create"}
        </button>
      </div>
    </div>
  );
}

function NewWebhookForm({ agentName, onDone }: { agentName: string; onDone: () => void }) {
  const [rows, setRows] = useState<FilterRow[]>([{ field: "event_type", op: "eq", value: "" }]);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () =>
      createTrigger(agentName, {
        trigger_type: "webhook",
        filter_conditions: rows.filter((r) => r.field.trim()).map((r) => ({ field: r.field.trim(), op: r.op, value: r.value })),
      }),
    onSuccess: (t) => {
      toast.success("Webhook trigger created");
      setCreatedUrl(t.webhook_url ?? null);
    },
    onError: () => toast.error("Failed to create webhook trigger"),
  });

  const update = (i: number, patch: Partial<FilterRow>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  if (createdUrl) {
    return (
      <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-4 mb-4 space-y-2">
        <p className="text-sm font-medium text-emerald-800">Copy this webhook URL now — it won&apos;t be shown again.</p>
        <div className="flex items-center gap-2">
          <code className="flex-1 text-xs bg-white border border-emerald-200 rounded px-2 py-1.5 break-all">{createdUrl}</code>
          <button onClick={() => { navigator.clipboard.writeText(createdUrl); setCopied(true); setTimeout(() => setCopied(false), 1500); }} className="btn-secondary text-xs py-1.5">
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
        <div className="flex justify-end">
          <button onClick={onDone} className="btn-primary text-xs py-1.5">Done</button>
        </div>
      </div>
    );
  }

  return (
    <div className="border border-slate-200 rounded-lg p-4 mb-4 space-y-2 bg-slate-50/50">
      <span className="text-xs text-slate-500 uppercase">Filter conditions (ALL must match; empty = match all)</span>
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <input className="flex-1 text-sm border border-slate-300 rounded px-2 py-1.5" value={row.field} onChange={(e) => update(i, { field: e.target.value })} placeholder="event_type" />
          <select className="text-sm border border-slate-300 rounded px-2 py-1.5 w-24" value={row.op} onChange={(e) => update(i, { op: e.target.value })}>
            {FILTER_OPS.map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
          <input className="flex-1 text-sm border border-slate-300 rounded px-2 py-1.5" value={row.value} onChange={(e) => update(i, { value: e.target.value })} placeholder="payment.fail" />
          <button onClick={() => setRows(rows.filter((_, idx) => idx !== i))} className="text-slate-400 hover:text-red-500"><Trash2 size={14} /></button>
        </div>
      ))}
      <button onClick={() => setRows([...rows, { field: "", op: "eq", value: "" }])} className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1">
        <Plus size={12} /> Add condition
      </button>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onDone} className="btn-secondary text-xs py-1.5">Cancel</button>
        <button onClick={() => create.mutate()} disabled={create.isPending} className="btn-primary text-xs py-1.5">
          {create.isPending ? "Creating…" : "Create"}
        </button>
      </div>
    </div>
  );
}

function WebhookRow({
  agentName,
  trigger,
}: {
  agentName: string;
  trigger: {
    id: string;
    enabled: boolean;
    filter_conditions: Record<string, unknown> | Record<string, unknown>[] | null;
    auth_mode?: "token" | "client_signed";
  };
}) {
  const [newUrl, setNewUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const rotate = useMutation({
    mutationFn: () => rotateToken(agentName, trigger.id),
    onSuccess: (res) => {
      setNewUrl(res.webhook_url);
      toast.success("Token rotated — copy the URL now; it won't be shown again.");
    },
    onError: () => toast.error("Failed to rotate token"),
  });

  const copy = () => {
    if (!newUrl) return;
    navigator.clipboard.writeText(newUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const authMode = trigger.auth_mode ?? "token";

  return (
    <div className="border border-slate-200 rounded-lg p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500 uppercase">Webhook token</span>
        <button
          onClick={() => rotate.mutate()}
          disabled={rotate.isPending}
          className="btn-secondary text-xs py-1.5 disabled:opacity-50"
        >
          <KeyRound size={12} />
          {rotate.isPending ? "Rotating…" : "Rotate Token"}
        </button>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-xs text-slate-500 uppercase">Auth mode</span>
        <span className={`text-xs font-mono px-1.5 py-0.5 rounded ${authMode === "client_signed" ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-slate-100 text-slate-600 border border-slate-200"}`}>
          {authMode}
        </span>
        <span className="text-xs text-slate-400">
          {authMode === "client_signed"
            ? "Each sender signs with its own client-id + secret."
            : "Legacy: one shared bearer token names no application."}
        </span>
      </div>

      {newUrl ? (
        <div className="flex items-center gap-2">
          <code className="flex-1 text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1.5 break-all">
            {newUrl}
          </code>
          <button onClick={copy} className="btn-secondary text-xs py-1.5">
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          Token is stored hashed and never displayed. Rotate to generate a new URL (shown once).
        </p>
      )}

      {trigger.filter_conditions != null && (
        <div>
          <span className="text-xs text-slate-500 uppercase">Filter conditions</span>
          <pre className="mt-1 text-xs bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto">
            {JSON.stringify(trigger.filter_conditions, null, 2)}
          </pre>
        </div>
      )}

      <ClientPanel triggerId={trigger.id} />
    </div>
  );
}

// WS-4 — the per-application allowlist for one webhook trigger. Each registered
// client signs with its own secret, so a compromised sender is revoked on its own
// rather than by rotating the token every other sender shares.
function ClientPanel({ triggerId }: { triggerId: string }) {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { data: clients = [], isLoading, isError } = useQuery({
    queryKey: ["trigger-clients", triggerId],
    queryFn: () => listTriggerClients(triggerId),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ["trigger-clients", triggerId] });

  const register = useMutation({
    mutationFn: () => createTriggerClient(triggerId, { client_id: clientId.trim() }),
    onSuccess: (res) => {
      // The only moment this value will ever exist in the UI — the read model has
      // no secret field, so a refetch cannot bring it back.
      setSecret(res.secret);
      setError(null);
      setClientId("");
      toast.success("Client registered — copy the secret now.");
      invalidate();
    },
    onError: (e) => {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setError(detail ?? "Failed to register client");
      toast.error(detail ?? "Failed to register client");
    },
  });

  const toggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) =>
      setClientEnabled(triggerId, id, enabled),
    onSuccess: (_r, v) => { toast.success(v.enabled ? "Client enabled" : "Client disabled"); invalidate(); },
    onError: () => toast.error("Failed to update client"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => deleteTriggerClient(triggerId, id),
    onSuccess: () => { toast.success("Client revoked"); invalidate(); },
    onError: () => toast.error("Failed to revoke client"),
  });

  const closeAdd = () => { setAdding(false); setSecret(null); setError(null); setClientId(""); };

  return (
    <div className="border-t border-slate-100 pt-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500 uppercase">Signing clients</span>
        {!adding && (
          <button onClick={() => setAdding(true)} className="btn-secondary text-xs py-1">
            <Plus size={12} /> Add client
          </button>
        )}
      </div>

      {adding && (secret ? (
        <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-4 space-y-2">
          <p className="text-sm font-medium text-emerald-800">
            Copy this signing secret now — it won&apos;t be shown again.
          </p>
          <div className="flex items-center gap-2">
            <code data-testid="client-secret" className="flex-1 text-xs bg-white border border-emerald-200 rounded px-2 py-1.5 break-all">
              {secret}
            </code>
            <button
              onClick={() => { navigator.clipboard.writeText(secret); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
              className="btn-secondary text-xs py-1.5"
              aria-label="Copy secret"
            >
              {copied ? <Check size={12} /> : <Copy size={12} />}
            </button>
          </div>
          <p className="text-xs text-emerald-700">
            It is stored encrypted for the gateway and is never retrievable. If you lose it, revoke this client and register a new one.
          </p>
          <div className="flex justify-end">
            <button onClick={closeAdd} className="btn-primary text-xs py-1.5">Done</button>
          </div>
        </div>
      ) : (
        <div className="border border-slate-200 rounded-lg p-4 space-y-2 bg-slate-50/50">
          <label className="block">
            <span className="text-xs text-slate-500 uppercase">Client ID — names the sending application</span>
            <input
              value={clientId}
              onChange={(e) => { setClientId(e.target.value); setError(null); }}
              placeholder="billing-service"
              className="mt-1 w-full font-mono text-sm border border-slate-300 rounded px-2 py-1.5"
            />
          </label>
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex justify-end gap-2">
            <button onClick={closeAdd} className="btn-secondary text-xs py-1.5">Cancel</button>
            <button
              onClick={() => register.mutate()}
              disabled={register.isPending || !clientId.trim()}
              className="btn-primary text-xs py-1.5 disabled:opacity-50"
            >
              {register.isPending ? "Registering…" : "Register"}
            </button>
          </div>
        </div>
      ))}

      {isLoading ? (
        <p className="text-xs text-slate-400">Loading clients…</p>
      ) : isError ? (
        <p className="text-xs text-red-600">Failed to load clients.</p>
      ) : clients.length === 0 ? (
        <p className="text-xs text-slate-400">
          No clients registered. Senders must present a registered client-id and signature.
        </p>
      ) : (
        <div className="space-y-2">
          {clients.map((c) => (
            <div key={c.client_id} data-testid={`client-row-${c.client_id}`} className="flex items-center justify-between gap-3 border border-slate-200 rounded px-3 py-2">
              <div className="min-w-0">
                <code className="text-xs text-slate-700">{c.client_id}</code>
                <p className="text-xs text-slate-400">
                  Registered by <span className="font-mono text-slate-500">{c.created_by ?? "unknown"}</span> on {new Date(c.created_at).toLocaleString()}
                </p>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <label className="inline-flex items-center gap-1.5 text-xs text-slate-700">
                  <input
                    type="checkbox"
                    checked={c.enabled}
                    disabled={toggle.isPending}
                    onChange={(e) => toggle.mutate({ id: c.client_id, enabled: e.target.checked })}
                    className="rounded"
                    aria-label={`Enabled ${c.client_id}`}
                  />
                  Enabled
                </label>
                <button
                  onClick={() => remove.mutate(c.client_id)}
                  disabled={remove.isPending}
                  className="text-slate-400 hover:text-red-500"
                  aria-label={`Revoke ${c.client_id}`}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function TriggerRow({
  agentName,
  trigger,
  onSaved,
}: {
  agentName: string;
  trigger: {
    id: string;
    cron_expression: string | null;
    timezone: string | null;
    enabled: boolean;
    alert_email?: string | null;
    alert_on_failure?: boolean;
    approver_role?: string | null;
    armed_by?: string | null;
  };
  onSaved: () => void;
}) {
  const [cron, setCron] = useState(trigger.cron_expression ?? "");
  const [tz, setTz] = useState(trigger.timezone ?? "UTC");
  const [enabled, setEnabled] = useState(trigger.enabled);
  const [alertEmail, setAlertEmail] = useState(trigger.alert_email ?? "");
  const [alertOnFailure, setAlertOnFailure] = useState(trigger.alert_on_failure ?? true);
  const [approverRole, setApproverRole] = useState(trigger.approver_role ?? "agent:reviewer");

  const save = useMutation({
    mutationFn: () =>
      updateTrigger(agentName, trigger.id, {
        cron_expression: cron,
        timezone: tz,
        enabled,
        alert_email: alertEmail.trim() === "" ? null : alertEmail.trim(),
        alert_on_failure: alertOnFailure,
        approver_role: approverRole || null,
      }),
    onSuccess: () => {
      toast.success("Trigger updated");
      onSaved();
    },
    onError: () => toast.error("Failed to update trigger"),
  });

  return (
    <div className="border border-slate-200 rounded-lg p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Cron expression</span>
          <input
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            placeholder="* * * * *"
            className="mt-1 w-full font-mono text-sm border border-slate-300 rounded px-2 py-1.5"
          />
        </label>
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Timezone</span>
          <select
            value={tz}
            onChange={(e) => setTz(e.target.value)}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          >
            {COMMON_TZ.map((z) => (
              <option key={z} value={z}>{z}</option>
            ))}
          </select>
        </label>
      </div>
      <div className="border-t border-slate-100 pt-3 space-y-2">
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Approver role — reviewer scope for this daemon run&apos;s approvals</span>
          <select value={approverRole} onChange={(e) => setApproverRole(e.target.value)}
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5">
            {APPROVER_ROLES.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        </label>
        {trigger.armed_by && (
          <p className="text-xs text-slate-400">
            Armed by <span className="font-mono text-slate-500">{trigger.armed_by}</span>
          </p>
        )}
      </div>
      <div className="border-t border-slate-100 pt-3 space-y-2">
        <span className="text-xs text-slate-500 uppercase">Failure alerts</span>
        <label className="block">
          <input
            type="email"
            value={alertEmail}
            onChange={(e) => setAlertEmail(e.target.value)}
            placeholder="alerts@example.com"
            className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
          />
        </label>
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={alertOnFailure}
            onChange={(e) => setAlertOnFailure(e.target.checked)}
            className="rounded"
          />
          Email me when a run fails
        </label>
      </div>
      <div className="flex items-center justify-between">
        <label className="inline-flex items-center gap-2 text-sm text-slate-700">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
            className="rounded"
          />
          Enabled
        </label>
        <button
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="btn-primary text-xs py-1.5 disabled:opacity-50"
        >
          {save.isPending ? "Saving…" : "Save"}
        </button>
      </div>
    </div>
  );
}
