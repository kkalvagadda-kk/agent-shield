import { useQuery } from '@tanstack/react-query';
import { Settings, Star, Wrench } from 'lucide-react';
import { listTools, listSkills } from '../api/registryApi';
import { useWorkflowStore } from '../stores/workflowStore';

// ---------------------------------------------------------------------------
// Field helper
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
// ToolSelector — multi-select from Registry tools
// ---------------------------------------------------------------------------
function ToolSelector({
  toolIds,
  onChange,
}: {
  toolIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const { data } = useQuery({
    queryKey: ['registry-tools'],
    queryFn: () => listTools(),
  });
  const tools = data?.items ?? [];

  return (
    <div className="space-y-1">
      {tools.map((tool) => (
        <label key={tool.id} className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={toolIds.includes(tool.id)}
            onChange={(e) => {
              if (e.target.checked) onChange([...toolIds, tool.id]);
              else onChange(toolIds.filter((id) => id !== tool.id));
            }}
          />
          <Wrench size={12} className="text-slate-400 shrink-0" />
          <span className="font-medium">{tool.display_name ?? tool.name}</span>
          {tool.description && (
            <span className="text-slate-400 truncate text-xs">{tool.description}</span>
          )}
        </label>
      ))}
      {tools.length === 0 && (
        <p className="text-xs text-slate-400">No tools registered yet. Add tools in the Registry.</p>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// SkillSelector — multi-select from Registry skills
// ---------------------------------------------------------------------------
function SkillSelector({
  skillIds,
  onChange,
}: {
  skillIds: string[];
  onChange: (ids: string[]) => void;
}) {
  const { data } = useQuery({
    queryKey: ['registry-skills'],
    queryFn: () => listSkills(),
  });
  const skills = data?.items ?? [];

  return (
    <div className="space-y-1">
      {skills.map((skill) => (
        <label key={skill.id} className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            checked={skillIds.includes(skill.id)}
            onChange={(e) => {
              if (e.target.checked) onChange([...skillIds, skill.id]);
              else onChange(skillIds.filter((id) => id !== skill.id));
            }}
          />
          <Star size={12} className="text-slate-400 shrink-0" />
          <span className="font-medium">{skill.name}</span>
          {skill.description && (
            <span className="text-slate-400 truncate text-xs">{skill.description}</span>
          )}
        </label>
      ))}
      {skills.length === 0 && (
        <p className="text-xs text-slate-400">No skills registered yet. Add skills in the Registry.</p>
      )}
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
  const toolIds = Array.isArray(config.tool_ids) ? (config.tool_ids as string[]) : [];
  const skillIds = Array.isArray(config.skill_ids) ? (config.skill_ids as string[]) : [];

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

      {/* Tools & Skills */}
      <div className="mt-4">
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wide mb-2">
          Tools &amp; Skills
        </p>

        <details className="mb-2">
          <summary className="text-sm font-medium text-slate-700 cursor-pointer select-none mb-1">
            Tools
          </summary>
          <div className="mt-1 pl-1">
            <ToolSelector
              toolIds={toolIds}
              onChange={(ids) => onChange({ tool_ids: ids })}
            />
          </div>
        </details>

        <details>
          <summary className="text-sm font-medium text-slate-700 cursor-pointer select-none mb-1">
            Skills
          </summary>
          <div className="mt-1 pl-1">
            <SkillSelector
              skillIds={skillIds}
              onChange={(ids) => onChange({ skill_ids: ids })}
            />
          </div>
        </details>
      </div>
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
// EdgePanel — shown when an edge is selected
// ---------------------------------------------------------------------------
function EdgePanel({ edgeId }: { edgeId: string }) {
  const store = useWorkflowStore();
  const edge = store.edges.find((e) => e.id === edgeId);
  const condition = String((edge?.data as Record<string, unknown> | undefined)?.condition ?? '');

  return (
    <Field label="Condition">
      <input
        className="input"
        value={condition}
        onChange={(e) => store.updateEdgeCondition(edgeId, e.target.value)}
        placeholder="e.g. refund_requested (blank = default)"
      />
      <p className="text-xs text-slate-400 mt-1">
        The agent routes to this target when its output contains this keyword.
        Leave blank for the default (fallback) path.
      </p>
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
    (selectedNode?.data as { config?: Record<string, unknown> } | undefined)?.config ?? {};

  // Determine header badge label
  const badgeLabel = store.selectedEdgeId
    ? 'edge'
    : selectedNode?.type ?? null;

  return (
    <div className="w-72 border-l border-slate-200 bg-white flex flex-col overflow-hidden shrink-0">
      <div className="px-4 py-3 border-b border-slate-200 flex items-center gap-2">
        <Settings size={14} className="text-slate-500" />
        <span className="text-sm font-semibold text-slate-700">Properties</span>
        {badgeLabel && (
          <span className="ml-auto badge bg-slate-100 text-slate-500 capitalize">
            {badgeLabel}
          </span>
        )}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {store.selectedEdgeId ? (
          <EdgePanel edgeId={store.selectedEdgeId} />
        ) : !selectedNode ? (
          <div className="flex flex-col items-center justify-center h-32 text-center text-slate-400">
            <Settings size={24} className="mb-2 opacity-30" />
            <p className="text-sm">Select a node or edge to edit its properties</p>
          </div>
        ) : selectedNode.type === 'agent' ? (
          <AgentPanel config={config} onChange={handleChange} />
        ) : selectedNode.type === 'end' ? (
          <EndPanel config={config} onChange={handleChange} />
        ) : (
          <p className="text-sm text-slate-400">Unknown node type: {selectedNode.type}</p>
        )}
      </div>
    </div>
  );
}
