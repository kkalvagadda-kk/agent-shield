import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X, KeyRound, Copy, Check, Plus, Trash2, Clock, Webhook } from 'lucide-react';
import {
  listWorkflowTriggers,
  createWorkflowTrigger,
  updateWorkflowTrigger,
  deleteWorkflowTrigger,
  rotateWorkflowToken,
  type AgentTrigger,
} from '../../api/registryApi';

const COMMON_TZ = [
  'UTC',
  'America/New_York',
  'America/Chicago',
  'America/Los_Angeles',
  'Europe/London',
  'Asia/Kolkata',
];
const FILTER_OPS = ['eq', 'neq', 'contains', 'gt', 'gte', 'lt', 'lte', 'exists', 'in'];
interface FilterRow {
  field: string;
  op: string;
  value: string;
}

interface Props {
  workflowId: string;
  workflowName: string;
  onClose: () => void;
}

export default function WorkflowTriggersPanel({ workflowId, workflowName, onClose }: Props) {
  const qc = useQueryClient();
  const { data: triggers = [], isLoading } = useQuery({
    queryKey: ['workflow-triggers', workflowId],
    queryFn: () => listWorkflowTriggers(workflowId),
  });
  const invalidate = () => qc.invalidateQueries({ queryKey: ['workflow-triggers', workflowId] });

  const [addSchedule, setAddSchedule] = useState(false);
  const [addWebhook, setAddWebhook] = useState(false);

  const schedules = triggers.filter((t) => t.trigger_type === 'schedule');
  const webhooks = triggers.filter((t) => t.trigger_type === 'webhook');

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 flex flex-col max-h-[85vh]">
        <div className="flex items-center justify-between mb-1">
          <h2 className="text-lg font-semibold text-slate-900">Workflow Triggers</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 transition-colors">
            <X size={18} />
          </button>
        </div>
        <p className="text-xs text-slate-400 mb-4">
          Schedule or webhook triggers fire this whole workflow ({workflowName}) — the scheduler and
          event gateway start a run just like they do for agents.
        </p>

        <div className="overflow-y-auto flex-1 -mx-1 px-1 space-y-6">
          {/* Schedules */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium text-slate-700 flex items-center gap-1.5">
                <Clock size={14} /> Schedules
              </h3>
              {!addSchedule && (
                <button
                  onClick={() => setAddSchedule(true)}
                  className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
                >
                  <Plus size={12} /> New schedule
                </button>
              )}
            </div>
            {addSchedule && (
              <NewScheduleForm
                workflowId={workflowId}
                onDone={() => {
                  setAddSchedule(false);
                  invalidate();
                }}
              />
            )}
            {isLoading && <p className="text-sm text-slate-400">Loading…</p>}
            {!isLoading && schedules.length === 0 && !addSchedule && (
              <p className="text-sm text-slate-500">No schedule triggers configured.</p>
            )}
            <div className="space-y-2">
              {schedules.map((t) => (
                <ScheduleRow
                  key={t.id}
                  workflowId={workflowId}
                  trigger={t}
                  onChanged={invalidate}
                />
              ))}
            </div>
          </section>

          {/* Webhooks */}
          <section>
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-sm font-medium text-slate-700 flex items-center gap-1.5">
                <Webhook size={14} /> Webhooks
              </h3>
              {!addWebhook && (
                <button
                  onClick={() => setAddWebhook(true)}
                  className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
                >
                  <Plus size={12} /> New webhook
                </button>
              )}
            </div>
            {addWebhook && (
              <NewWebhookForm
                workflowId={workflowId}
                onDone={() => {
                  setAddWebhook(false);
                  invalidate();
                }}
              />
            )}
            {!isLoading && webhooks.length === 0 && !addWebhook && (
              <p className="text-sm text-slate-500">No webhook triggers configured.</p>
            )}
            <div className="space-y-2">
              {webhooks.map((t) => (
                <WebhookRow
                  key={t.id}
                  workflowId={workflowId}
                  trigger={t}
                  onChanged={invalidate}
                />
              ))}
            </div>
          </section>
        </div>

        <div className="flex justify-end mt-4 pt-3 border-t border-slate-100">
          <button onClick={onClose} className="btn-secondary">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

function NewScheduleForm({ workflowId, onDone }: { workflowId: string; onDone: () => void }) {
  const [cron, setCron] = useState('0 9 * * 1');
  const [tz, setTz] = useState('UTC');
  const [alertEmail, setAlertEmail] = useState('');
  const [payload, setPayload] = useState('');
  const payloadError = payload.trim()
    ? (() => {
        try {
          JSON.parse(payload);
          return null;
        } catch (e) {
          return e instanceof Error ? e.message : 'parse error';
        }
      })()
    : null;
  const create = useMutation({
    mutationFn: () =>
      createWorkflowTrigger(workflowId, {
        trigger_type: 'schedule',
        cron_expression: cron,
        timezone: tz,
        alert_email: alertEmail.trim() || null,
        ...(payload.trim() ? { input_payload: JSON.parse(payload) } : {}),
      }),
    onSuccess: () => {
      toast.success('Schedule trigger created');
      onDone();
    },
    onError: () => toast.error('Failed to create schedule trigger'),
  });
  return (
    <div className="border border-slate-200 rounded-lg p-4 mb-3 space-y-3 bg-slate-50/50">
      <div className="grid grid-cols-2 gap-3">
        <label className="block">
          <span className="text-xs text-slate-500 uppercase">Cron expression</span>
          <input
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            placeholder="0 9 * * 1"
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
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Failure alert email (optional)</span>
        <input
          type="email"
          value={alertEmail}
          onChange={(e) => setAlertEmail(e.target.value)}
          placeholder="oncall@example.com"
          className="mt-1 w-full text-sm border border-slate-300 rounded px-2 py-1.5"
        />
      </label>
      <label className="block">
        <span className="text-xs text-slate-500 uppercase">Input payload — JSON job spec (optional)</span>
        <textarea
          value={payload}
          onChange={(e) => setPayload(e.target.value)}
          rows={3}
          placeholder={'{ "task": "weekly-report" }'}
          className="mt-1 w-full font-mono text-xs border border-slate-300 rounded px-2 py-1.5 resize-none"
        />
        {payloadError ? (
          <p className="text-xs text-red-600 mt-0.5">Invalid JSON: {payloadError}</p>
        ) : (
          <p className="text-xs text-slate-400 mt-0.5">
            Fed to the workflow as its input on each fire.
          </p>
        )}
      </label>
      <div className="flex justify-end gap-2">
        <button onClick={onDone} className="btn-secondary text-xs py-1.5">
          Cancel
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending || !!payloadError}
          className="btn-primary text-xs py-1.5"
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  );
}

function NewWebhookForm({ workflowId, onDone }: { workflowId: string; onDone: () => void }) {
  const [rows, setRows] = useState<FilterRow[]>([{ field: 'event_type', op: 'eq', value: '' }]);
  const [createdUrl, setCreatedUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const create = useMutation({
    mutationFn: () =>
      createWorkflowTrigger(workflowId, {
        trigger_type: 'webhook',
        filter_conditions: rows
          .filter((r) => r.field.trim())
          .map((r) => ({ field: r.field.trim(), op: r.op, value: r.value })),
      }),
    onSuccess: (t) => {
      toast.success('Webhook trigger created');
      setCreatedUrl(t.webhook_url ?? null);
    },
    onError: () => toast.error('Failed to create webhook trigger'),
  });

  const update = (i: number, patch: Partial<FilterRow>) =>
    setRows(rows.map((r, idx) => (idx === i ? { ...r, ...patch } : r)));

  if (createdUrl) {
    return (
      <div className="border border-emerald-200 bg-emerald-50 rounded-lg p-4 mb-3 space-y-2">
        <p className="text-sm font-medium text-emerald-800">
          Copy this webhook URL now — it won&apos;t be shown again.
        </p>
        <div className="flex items-center gap-2">
          <code className="flex-1 text-xs bg-white border border-emerald-200 rounded px-2 py-1.5 break-all">
            {createdUrl}
          </code>
          <button
            onClick={() => {
              navigator.clipboard.writeText(createdUrl);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="btn-secondary text-xs py-1.5"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
        <div className="flex justify-end">
          <button onClick={onDone} className="btn-primary text-xs py-1.5">
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="border border-slate-200 rounded-lg p-4 mb-3 space-y-2 bg-slate-50/50">
      <span className="text-xs text-slate-500 uppercase">
        Filter conditions (ALL must match; empty = match all)
      </span>
      {rows.map((row, i) => (
        <div key={i} className="flex items-center gap-2">
          <input
            className="flex-1 text-sm border border-slate-300 rounded px-2 py-1.5"
            value={row.field}
            onChange={(e) => update(i, { field: e.target.value })}
            placeholder="event_type"
          />
          <select
            className="text-sm border border-slate-300 rounded px-2 py-1.5 w-24"
            value={row.op}
            onChange={(e) => update(i, { op: e.target.value })}
          >
            {FILTER_OPS.map((o) => (
              <option key={o} value={o}>
                {o}
              </option>
            ))}
          </select>
          <input
            className="flex-1 text-sm border border-slate-300 rounded px-2 py-1.5"
            value={row.value}
            onChange={(e) => update(i, { value: e.target.value })}
            placeholder="payment.fail"
          />
          <button
            onClick={() => setRows(rows.filter((_, idx) => idx !== i))}
            className="text-slate-400 hover:text-red-500"
          >
            <Trash2 size={14} />
          </button>
        </div>
      ))}
      <button
        onClick={() => setRows([...rows, { field: '', op: 'eq', value: '' }])}
        className="text-xs text-indigo-600 hover:text-indigo-800 inline-flex items-center gap-1"
      >
        <Plus size={12} /> Add condition
      </button>
      <div className="flex justify-end gap-2 pt-1">
        <button onClick={onDone} className="btn-secondary text-xs py-1.5">
          Cancel
        </button>
        <button
          onClick={() => create.mutate()}
          disabled={create.isPending}
          className="btn-primary text-xs py-1.5"
        >
          {create.isPending ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  );
}

function ScheduleRow({
  workflowId,
  trigger,
  onChanged,
}: {
  workflowId: string;
  trigger: AgentTrigger;
  onChanged: () => void;
}) {
  const toggle = useMutation({
    mutationFn: () => updateWorkflowTrigger(workflowId, trigger.id, { enabled: !trigger.enabled }),
    onSuccess: () => {
      toast.success('Trigger updated');
      onChanged();
    },
    onError: () => toast.error('Failed to update trigger'),
  });
  const del = useMutation({
    mutationFn: () => deleteWorkflowTrigger(workflowId, trigger.id),
    onSuccess: () => {
      toast.success('Trigger deleted');
      onChanged();
    },
    onError: () => toast.error('Failed to delete trigger'),
  });
  return (
    <div className="border border-slate-200 rounded-lg p-3 flex items-center justify-between gap-3">
      <div className="min-w-0">
        <code className="text-sm font-mono text-slate-700">{trigger.cron_expression}</code>
        <span className="text-xs text-slate-400 ml-2">{trigger.timezone}</span>
        {trigger.input_payload && (
          <p className="text-[11px] text-slate-400 truncate">
            payload: {JSON.stringify(trigger.input_payload)}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <button
          onClick={() => toggle.mutate()}
          disabled={toggle.isPending}
          className={`text-xs px-2 py-1 rounded ${
            trigger.enabled ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'
          }`}
        >
          {trigger.enabled ? 'Enabled' : 'Disabled'}
        </button>
        <button
          onClick={() => del.mutate()}
          disabled={del.isPending}
          className="text-slate-400 hover:text-red-500"
        >
          <Trash2 size={14} />
        </button>
      </div>
    </div>
  );
}

function WebhookRow({
  workflowId,
  trigger,
  onChanged,
}: {
  workflowId: string;
  trigger: AgentTrigger;
  onChanged: () => void;
}) {
  const [newUrl, setNewUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const rotate = useMutation({
    mutationFn: () => rotateWorkflowToken(workflowId, trigger.id),
    onSuccess: (res) => {
      setNewUrl(res.webhook_url);
      toast.success("Token rotated — copy the URL now; it won't be shown again.");
    },
    onError: () => toast.error('Failed to rotate token'),
  });
  const del = useMutation({
    mutationFn: () => deleteWorkflowTrigger(workflowId, trigger.id),
    onSuccess: () => {
      toast.success('Trigger deleted');
      onChanged();
    },
    onError: () => toast.error('Failed to delete trigger'),
  });

  return (
    <div className="border border-slate-200 rounded-lg p-3 space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-xs text-slate-500 uppercase">Webhook token</span>
        <div className="flex items-center gap-2">
          <button
            onClick={() => rotate.mutate()}
            disabled={rotate.isPending}
            className="btn-secondary text-xs py-1.5 disabled:opacity-50"
          >
            <KeyRound size={12} />
            {rotate.isPending ? 'Rotating…' : 'Rotate Token'}
          </button>
          <button
            onClick={() => del.mutate()}
            disabled={del.isPending}
            className="text-slate-400 hover:text-red-500"
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      {newUrl ? (
        <div className="flex items-center gap-2">
          <code className="flex-1 text-xs bg-slate-50 border border-slate-200 rounded px-2 py-1.5 break-all">
            {newUrl}
          </code>
          <button
            onClick={() => {
              navigator.clipboard.writeText(newUrl);
              setCopied(true);
              setTimeout(() => setCopied(false), 1500);
            }}
            className="btn-secondary text-xs py-1.5"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      ) : (
        <p className="text-xs text-slate-400">
          Token is stored hashed and never displayed. Rotate to generate a new URL (shown once).
        </p>
      )}
      {trigger.filter_conditions != null && (
        <pre className="text-xs bg-slate-50 border border-slate-200 rounded p-2 overflow-x-auto">
          {JSON.stringify(trigger.filter_conditions, null, 2)}
        </pre>
      )}
    </div>
  );
}
