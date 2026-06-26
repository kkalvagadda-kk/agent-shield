import { useQuery } from '@tanstack/react-query';
import { Settings } from 'lucide-react';
import { listAuthConfigs } from '../api/registryApi';
import { useWorkflowStore } from '../stores/workflowStore';

// ---------------------------------------------------------------------------
// Field helpers
// ---------------------------------------------------------------------------
function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-3">
      <label className="label">{label}</label>
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-panels for each node type
// ---------------------------------------------------------------------------
function AgentPanel({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (partial: Record<string, unknown>) => void;
}) {
  return (
    <>
      <Field label="Name">
        <input
          className="input"
          value={String(config.name ?? '')}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="agent-name"
        />
      </Field>
      <Field label="Instructions">
        <textarea
          className="input resize-none"
          rows={4}
          value={String(config.instructions ?? '')}
          onChange={(e) => onChange({ instructions: e.target.value })}
          placeholder="Describe what this agent does…"
        />
      </Field>
      <Field label="Model">
        <select
          className="input"
          value={String(config.model ?? 'claude-sonnet-4-6')}
          onChange={(e) => onChange({ model: e.target.value })}
        >
          <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
          <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
          <option value="claude-opus-4-8">claude-opus-4-8</option>
        </select>
      </Field>
      <Field label="Risk">
        <select
          className="input"
          value={String(config.risk ?? 'low')}
          onChange={(e) => onChange({ risk: e.target.value })}
        >
          <option value="low">Low</option>
          <option value="high">High</option>
        </select>
      </Field>
    </>
  );
}

function HttpToolPanel({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (partial: Record<string, unknown>) => void;
}) {
  const { data: authConfigsData } = useQuery({
    queryKey: ['auth-configs'],
    queryFn: listAuthConfigs,
  });

  const headersValue =
    typeof config.headers === 'object' && config.headers !== null
      ? JSON.stringify(config.headers, null, 2)
      : String(config.headers ?? '{}');

  const handleHeadersChange = (raw: string) => {
    try {
      onChange({ headers: JSON.parse(raw) });
    } catch {
      // Keep raw string while user is typing; don't update store yet
    }
  };

  return (
    <>
      <Field label="Name">
        <input
          className="input"
          value={String(config.name ?? '')}
          onChange={(e) => onChange({ name: e.target.value })}
          placeholder="tool-name"
        />
      </Field>
      <Field label="Endpoint">
        <input
          className="input"
          value={String(config.endpoint ?? '')}
          onChange={(e) => onChange({ endpoint: e.target.value })}
          placeholder="https://api.example.com/resource"
        />
      </Field>
      <Field label="Method">
        <select
          className="input"
          value={String(config.method ?? 'GET')}
          onChange={(e) => onChange({ method: e.target.value })}
        >
          <option value="GET">GET</option>
          <option value="POST">POST</option>
          <option value="PUT">PUT</option>
          <option value="DELETE">DELETE</option>
          <option value="PATCH">PATCH</option>
        </select>
      </Field>
      <Field label="Headers (JSON)">
        <textarea
          className="input font-mono text-xs resize-none"
          rows={3}
          defaultValue={headersValue}
          onBlur={(e) => handleHeadersChange(e.target.value)}
          placeholder="{}"
        />
      </Field>
      <Field label="Body Template">
        <textarea
          className="input font-mono text-xs resize-none"
          rows={3}
          value={String(config.body_template ?? '')}
          onChange={(e) => onChange({ body_template: e.target.value })}
          placeholder="Leave empty for GET requests"
        />
      </Field>
      <Field label="Risk">
        <select
          className="input"
          value={String(config.risk ?? 'low')}
          onChange={(e) => onChange({ risk: e.target.value })}
        >
          <option value="low">Low</option>
          <option value="high">High</option>
        </select>
      </Field>
      <Field label="Auth Config">
        <select
          className="input"
          value={String(config.auth_config_id ?? '')}
          onChange={(e) =>
            onChange({ auth_config_id: e.target.value || null })
          }
        >
          <option value="">None</option>
          {authConfigsData?.items.map((ac) => (
            <option key={ac.id} value={ac.id}>
              {ac.name} ({ac.type})
            </option>
          ))}
        </select>
      </Field>
    </>
  );
}

function EndPanel({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (partial: Record<string, unknown>) => void;
}) {
  const mappingValue =
    typeof config.output_mapping === 'object' && config.output_mapping !== null
      ? JSON.stringify(config.output_mapping, null, 2)
      : String(config.output_mapping ?? '{}');

  const handleChange = (raw: string) => {
    try {
      onChange({ output_mapping: JSON.parse(raw) });
    } catch {
      // Keep editing without updating store until valid JSON
    }
  };

  return (
    <Field label="Output Mapping (JSON)">
      <textarea
        className="input font-mono text-xs resize-none"
        rows={5}
        defaultValue={mappingValue}
        onBlur={(e) => handleChange(e.target.value)}
        placeholder="{}"
      />
    </Field>
  );
}

// ---------------------------------------------------------------------------
// PropertiesPanel
// ---------------------------------------------------------------------------
export default function PropertiesPanel() {
  const store = useWorkflowStore();
  const selectedNode = store.nodes.find((n) => n.id === store.selectedNodeId);

  const handleChange = (partial: Record<string, unknown>) => {
    if (!store.selectedNodeId) return;
    store.updateNodeConfig(store.selectedNodeId, partial);
  };

  const config =
    (selectedNode?.data as { config?: Record<string, unknown> } | undefined)
      ?.config ?? {};

  return (
    <div className="w-72 border-l border-slate-200 bg-white flex flex-col overflow-hidden shrink-0">
      <div className="px-4 py-3 border-b border-slate-200 flex items-center gap-2">
        <Settings size={14} className="text-slate-500" />
        <span className="text-sm font-semibold text-slate-700">Properties</span>
        {selectedNode && (
          <span className="ml-auto badge bg-slate-100 text-slate-500 capitalize">
            {selectedNode.type}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {!selectedNode ? (
          <div className="flex flex-col items-center justify-center h-32 text-center text-slate-400">
            <Settings size={24} className="mb-2 opacity-30" />
            <p className="text-sm">Select a node to edit its properties</p>
          </div>
        ) : selectedNode.type === 'agent' ? (
          <AgentPanel config={config} onChange={handleChange} />
        ) : selectedNode.type === 'http_tool' ? (
          <HttpToolPanel config={config} onChange={handleChange} />
        ) : selectedNode.type === 'end' ? (
          <EndPanel config={config} onChange={handleChange} />
        ) : (
          <p className="text-sm text-slate-400">Unknown node type: {selectedNode.type}</p>
        )}
      </div>
    </div>
  );
}
