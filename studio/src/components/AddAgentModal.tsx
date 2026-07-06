import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { X, Search, Loader2, Bot, Plus } from 'lucide-react';
import { toast } from 'sonner';
import { listAgents, createAgent, type Agent } from '../api/registryApi';

/** Agent handed back to the caller; `is_inline` marks agents created in this modal. */
export type AddedAgent = Agent & { is_inline?: boolean };

interface AddAgentModalProps {
  /** Filter existing agents to this team + team for inline-created agents. Empty = all / 'default'. */
  team: string;
  onAdd: (agent: AddedAgent) => void;
  onClose: () => void;
  alreadyAddedIds: string[];
}

type Tab = 'existing' | 'new';

export default function AddAgentModal({
  team,
  onAdd,
  onClose,
  alreadyAddedIds,
}: AddAgentModalProps) {
  const [tab, setTab] = useState<Tab>('existing');
  const [search, setSearch] = useState('');

  const { data, isLoading } = useQuery({
    queryKey: ['agents-for-workflow', team, 'composable'],
    // Members must be composable: reactive/durable agents with NO self-firing
    // schedule/webhook trigger (else they'd double-fire — once on their own
    // trigger, once via the orchestrator).
    queryFn: () => listAgents(100, 0, undefined, { composable: true }),
  });

  const agents = (data?.items ?? []).filter((a) => {
    const matchesTeam = team ? a.team === team : true;
    const matchesSearch = search
      ? a.name.toLowerCase().includes(search.toLowerCase())
      : true;
    return matchesTeam && matchesSearch;
  });

  return (
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6 flex flex-col max-h-[80vh]">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold text-slate-900">Add Agent to Workflow</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        {/* Tabs */}
        <div className="flex gap-1 mb-4 border-b border-slate-200">
          <button
            onClick={() => setTab('existing')}
            className={`px-3 py-1.5 text-sm font-medium -mb-px border-b-2 transition-colors ${
              tab === 'existing'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            Existing Agent
          </button>
          <button
            onClick={() => setTab('new')}
            className={`px-3 py-1.5 text-sm font-medium -mb-px border-b-2 transition-colors ${
              tab === 'new'
                ? 'border-blue-500 text-blue-600'
                : 'border-transparent text-slate-500 hover:text-slate-700'
            }`}
          >
            Create New Agent
          </button>
        </div>

        {tab === 'existing' ? (
          <ExistingTab
            team={team}
            search={search}
            setSearch={setSearch}
            isLoading={isLoading}
            agents={agents}
            alreadyAddedIds={alreadyAddedIds}
            onAdd={onAdd}
          />
        ) : (
          <NewAgentTab team={team} onCreated={onAdd} />
        )}

        <div className="flex justify-end mt-4 pt-3 border-t border-slate-100">
          <button onClick={onClose} className="btn-secondary">
            Done
          </button>
        </div>
      </div>
    </div>
  );
}

function ExistingTab({
  team,
  search,
  setSearch,
  isLoading,
  agents,
  alreadyAddedIds,
  onAdd,
}: {
  team: string;
  search: string;
  setSearch: (s: string) => void;
  isLoading: boolean;
  agents: Agent[];
  alreadyAddedIds: string[];
  onAdd: (a: AddedAgent) => void;
}) {
  return (
    <>
      {team ? (
        <p className="text-xs text-slate-400 mb-3">
          Showing agents from team: <span className="font-medium text-slate-600">{team}</span>
        </p>
      ) : (
        <p className="text-xs text-slate-400 mb-3">
          Showing all agents — the first agent you add sets the workflow team.
        </p>
      )}
      <p className="text-xs text-slate-400 mb-3">
        Only composable agents are listed — agents with their own schedule or
        webhook trigger are hidden (they'd fire twice).
      </p>

      <div className="relative mb-3">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
        <input
          className="input pl-8"
          placeholder="Search agents…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          autoFocus
        />
      </div>

      <div className="overflow-y-auto flex-1 -mx-1 px-1">
        {isLoading && (
          <div className="flex items-center justify-center py-8 text-slate-400">
            <Loader2 size={18} className="animate-spin mr-2" />
            Loading agents…
          </div>
        )}
        {!isLoading && agents.length === 0 && (
          <div className="text-center py-8 text-slate-400 text-sm">
            {search
              ? 'No agents match your search.'
              : team
                ? `No agents found for team "${team}".`
                : 'No agents found.'}
          </div>
        )}
        {!isLoading &&
          agents.map((agent) => {
            const isAdded = alreadyAddedIds.includes(agent.id);
            return (
              <div
                key={agent.id}
                className="flex items-start justify-between gap-3 p-3 rounded-lg hover:bg-slate-50 transition-colors border border-transparent hover:border-slate-100 mb-1"
              >
                <div className="flex items-start gap-2 min-w-0">
                  <Bot size={16} className="text-blue-400 shrink-0 mt-0.5" />
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">{agent.name}</p>
                    {agent.description && (
                      <p className="text-xs text-slate-400 truncate max-w-xs">{agent.description}</p>
                    )}
                    <div className="flex items-center gap-1 mt-0.5">
                      <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 py-0.5 rounded-full">
                        {agent.execution_shape}
                      </span>
                      <span className="text-[10px] bg-slate-50 text-slate-500 px-1.5 py-0.5 rounded-full">
                        {agent.team}
                      </span>
                    </div>
                  </div>
                </div>
                <button
                  disabled={isAdded}
                  onClick={() => !isAdded && onAdd(agent)}
                  className={`btn-primary text-xs py-1 px-2.5 shrink-0 whitespace-nowrap ${
                    isAdded ? 'opacity-50 cursor-not-allowed' : ''
                  }`}
                >
                  {isAdded ? 'Added' : '+ Add'}
                </button>
              </div>
            );
          })}
      </div>
    </>
  );
}

function NewAgentTab({ team, onCreated }: { team: string; onCreated: (a: AddedAgent) => void }) {
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [instructions, setInstructions] = useState('');
  const [model, setModel] = useState('claude-sonnet-4-6');
  const [shape, setShape] = useState<'reactive' | 'durable'>('reactive');
  const [creating, setCreating] = useState(false);

  const nameValid = /^[a-z0-9-]+$/.test(name);

  const handleCreate = async () => {
    if (!nameValid) {
      toast.error('Name must be lowercase letters, numbers, and hyphens.');
      return;
    }
    setCreating(true);
    try {
      const agent = await createAgent({
        name: name.trim(),
        team: team || 'default',
        description: description || undefined,
        agent_type: 'declarative',
        execution_shape: shape,
        metadata: { instructions, model },
      });
      toast.success(`Agent "${agent.name}" created and added.`);
      onCreated({ ...agent, is_inline: true });
    } catch (err) {
      toast.error(`Failed to create agent: ${String(err)}`);
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="overflow-y-auto flex-1 -mx-1 px-1 space-y-3">
      <p className="text-xs text-slate-400">
        Creates a real, shareable agent (visible in Agents) and adds it to this workflow. Deploy it
        before running the workflow.
      </p>
      <div>
        <label className="label">Agent name</label>
        <input
          className="input"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="my-agent"
          autoFocus
        />
        {name && !nameValid && (
          <p className="text-xs text-red-600 mt-0.5">Lowercase letters, numbers, and hyphens only.</p>
        )}
      </div>
      <div>
        <label className="label">Description</label>
        <input
          className="input"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What does this agent do?"
        />
      </div>
      <div>
        <label className="label">Instructions</label>
        <textarea
          className="input resize-none"
          rows={5}
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="System prompt for this agent…"
        />
      </div>
      <div>
        <label className="label">Execution shape</label>
        <div className="flex gap-2">
          {(['reactive', 'durable'] as const).map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setShape(s)}
              className={`flex-1 text-xs py-1.5 px-2 rounded-lg border capitalize transition-colors ${
                shape === s
                  ? 'border-blue-500 bg-blue-50 text-blue-700 font-medium'
                  : 'border-slate-200 text-slate-500 hover:border-slate-300'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <p className="text-[11px] text-slate-400 mt-1">
          Members can't be scheduled or event-driven — the workflow owns triggering.
        </p>
      </div>
      <div>
        <label className="label">Model</label>
        <select className="input" value={model} onChange={(e) => setModel(e.target.value)}>
          <option value="claude-sonnet-4-6">claude-sonnet-4-6</option>
          <option value="claude-haiku-4-5-20251001">claude-haiku-4-5-20251001</option>
          <option value="claude-opus-4-8">claude-opus-4-8</option>
        </select>
      </div>
      <button
        onClick={handleCreate}
        disabled={creating || !name || !nameValid}
        className="btn-primary w-full text-sm"
      >
        {creating ? <Loader2 size={13} className="animate-spin" /> : <Plus size={13} />}
        {creating ? 'Creating…' : 'Create & Add'}
      </button>
    </div>
  );
}
