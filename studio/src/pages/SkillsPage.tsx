import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Loader2, Pencil, Plus, Star, Trash2, Wrench, X } from 'lucide-react';
import { useState } from 'react';
import { toast } from 'sonner';
import {
  createSkill,
  deleteSkill,
  listSkills,
  listTeams,
  listTools,
  updateSkill,
  type Skill,
} from '../api/registryApi';
import { cn } from '../lib/utils';

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function SkillsPage() {
  const qc = useQueryClient();
  const [editingSkill, setEditingSkill] = useState<Skill | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);

  const { data, isLoading, error } = useQuery({
    queryKey: ['skills'],
    queryFn: () => listSkills(),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteSkill(id),
    onSuccess: () => {
      toast.success('Skill deleted.');
      qc.invalidateQueries({ queryKey: ['skills'] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? 'Failed to delete skill.');
    },
  });

  const skills: Skill[] = data?.items ?? [];

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Skills</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Skill bundles — group tools together so agents can acquire them as a set
          </p>
        </div>
        <button
          onClick={() => {
            setEditingSkill(null);
            setShowCreateForm(true);
          }}
          className="btn-primary"
        >
          <Plus size={14} />
          New Skill
        </button>
      </div>

      {/* Create / Edit form */}
      {(showCreateForm || editingSkill) && (
        <SkillForm
          skill={editingSkill}
          onClose={() => {
            setShowCreateForm(false);
            setEditingSkill(null);
          }}
          onSaved={() => {
            setShowCreateForm(false);
            setEditingSkill(null);
            qc.invalidateQueries({ queryKey: ['skills'] });
          }}
        />
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading skills…
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load skills: {String(error)}
        </div>
      )}

      {/* Content */}
      {!isLoading && !error && (
        skills.length === 0 ? (
          <div className="card flex flex-col items-center py-16 text-center">
            <Star size={40} className="text-slate-300 mb-3" />
            <p className="text-slate-500 font-medium">No skills yet.</p>
            <button
              onClick={() => setShowCreateForm(true)}
              className="btn-primary mt-5"
            >
              <Plus size={14} />
              New Skill
            </button>
          </div>
        ) : (
          <div className="card p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {['Name', 'Team', 'Tools', 'Status', ''].map((h) => (
                    <th
                      key={h}
                      className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                    >
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {skills.map((skill) => (
                  <tr key={skill.id} className="hover:bg-slate-50 transition-colors">
                    <td className="px-4 py-3">
                      <p className="font-semibold text-slate-900">{skill.name}</p>
                      {skill.description && (
                        <p className="text-xs text-slate-400 truncate max-w-xs">
                          {skill.description}
                        </p>
                      )}
                    </td>
                    <td className="px-4 py-3 text-slate-600">{skill.team}</td>
                    <td className="px-4 py-3 text-slate-600">
                      <span className="inline-flex items-center gap-1">
                        <Wrench size={12} className="text-slate-400" />
                        {skill.tool_ids.length}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      <span className="badge bg-slate-100 text-slate-600 capitalize">
                        {skill.status}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-3">
                        <button
                          onClick={() => {
                            setShowCreateForm(false);
                            setEditingSkill(skill);
                          }}
                          className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-900 transition-colors"
                        >
                          <Pencil size={12} />
                          Edit
                        </button>
                        <button
                          onClick={() => {
                            if (confirm(`Delete skill "${skill.name}"?`)) {
                              deleteMutation.mutate(skill.id);
                            }
                          }}
                          disabled={
                            deleteMutation.isPending &&
                            deleteMutation.variables === skill.id
                          }
                          className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 disabled:opacity-50 transition-colors"
                        >
                          {deleteMutation.isPending &&
                          deleteMutation.variables === skill.id ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <Trash2 size={12} />
                          )}
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Create / Edit form
// ---------------------------------------------------------------------------
function SkillForm({
  skill,
  onClose,
  onSaved,
}: {
  skill: Skill | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const isEdit = !!skill;

  const { data: teamsData } = useQuery({
    queryKey: ['teams'],
    queryFn: listTeams,
  });

  const { data: toolsData } = useQuery({
    queryKey: ['registry-tools'],
    queryFn: () => listTools(),
  });

  const teams = teamsData?.items ?? [];
  const tools = toolsData?.items ?? [];

  const [name, setName] = useState(skill?.name ?? '');
  const [team, setTeam] = useState(skill?.team ?? '');
  const [description, setDescription] = useState(skill?.description ?? '');
  const [toolIds, setToolIds] = useState<string[]>(skill?.tool_ids ?? []);
  const [nameError, setNameError] = useState('');
  const [teamError, setTeamError] = useState('');

  const createMutation = useMutation({
    mutationFn: () =>
      createSkill({ name, team, description: description || undefined, tool_ids: toolIds }),
    onSuccess: () => {
      toast.success('Skill created.');
      onSaved();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? 'Failed to create skill.');
    },
  });

  const updateMutation = useMutation({
    mutationFn: () =>
      updateSkill(skill!.id, {
        name,
        description: description || undefined,
        tool_ids: toolIds,
      }),
    onSuccess: () => {
      toast.success('Skill updated.');
      onSaved();
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? 'Failed to update skill.');
    },
  });

  const isPending = createMutation.isPending || updateMutation.isPending;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    let valid = true;
    if (!name.trim()) {
      setNameError('Name is required');
      valid = false;
    } else {
      setNameError('');
    }
    if (!isEdit && !team) {
      setTeamError('Team is required');
      valid = false;
    } else {
      setTeamError('');
    }
    if (!valid) return;
    if (isEdit) {
      updateMutation.mutate();
    } else {
      createMutation.mutate();
    }
  };

  const toggleTool = (id: string) => {
    setToolIds((prev) =>
      prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]
    );
  };

  return (
    <div className="card mb-6 relative">
      <button
        onClick={onClose}
        className="absolute top-4 right-4 text-slate-400 hover:text-slate-700"
      >
        <X size={16} />
      </button>
      <h2 className="text-lg font-semibold text-slate-900 mb-5">
        {isEdit ? `Edit Skill — ${skill.name}` : 'New Skill'}
      </h2>

      <form onSubmit={handleSubmit} className="space-y-4" noValidate>
        <div className="grid grid-cols-2 gap-4">
          {/* Name */}
          <div className="space-y-1">
            <label className="label">
              Name <span className="text-red-500">*</span>
            </label>
            <input
              className={cn('input', nameError && 'border-red-400')}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="order-management"
            />
            {nameError && <p className="text-xs text-red-600">{nameError}</p>}
          </div>

          {/* Team */}
          <div className="space-y-1">
            <label className="label">
              Team {!isEdit && <span className="text-red-500">*</span>}
            </label>
            {isEdit ? (
              <input
                className="input bg-slate-50 text-slate-500 cursor-not-allowed"
                value={skill.team}
                disabled
              />
            ) : (
              <select
                className={cn('input', teamError && 'border-red-400')}
                value={team}
                onChange={(e) => setTeam(e.target.value)}
              >
                <option value="">— select team —</option>
                {teams.map((t) => (
                  <option key={t.id} value={t.name}>
                    {t.name}
                  </option>
                ))}
              </select>
            )}
            {teamError && <p className="text-xs text-red-600">{teamError}</p>}
          </div>
        </div>

        {/* Description */}
        <div className="space-y-1">
          <label className="label">Description</label>
          <input
            className="input"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Handles order retrieval and status updates"
          />
        </div>

        {/* Tools */}
        <div className="space-y-2">
          <label className="label">Tools</label>
          {tools.length === 0 ? (
            <p className="text-xs text-slate-400">
              No tools registered yet. Add tools first.
            </p>
          ) : (
            <div className="space-y-1 max-h-48 overflow-y-auto border border-slate-200 rounded-md p-3">
              {tools.map((tool) => (
                <label
                  key={tool.id}
                  className="flex items-center gap-2 text-sm cursor-pointer py-0.5"
                >
                  <input
                    type="checkbox"
                    checked={toolIds.includes(tool.id)}
                    onChange={() => toggleTool(tool.id)}
                  />
                  <Wrench size={12} className="text-slate-400 shrink-0" />
                  <span className="font-medium">
                    {tool.display_name ?? tool.name}
                  </span>
                  {tool.description && (
                    <span className="text-slate-400 text-xs truncate">
                      {tool.description}
                    </span>
                  )}
                </label>
              ))}
            </div>
          )}
          <p className="text-xs text-slate-400">{toolIds.length} selected</p>
        </div>

        <div className="flex justify-end gap-3 pt-2 border-t border-slate-100">
          <button type="button" onClick={onClose} className="btn-secondary">
            Cancel
          </button>
          <button
            type="submit"
            disabled={isPending}
            className="btn-primary"
          >
            {isPending ? (
              <>
                <Loader2 size={14} className="animate-spin" /> Saving…
              </>
            ) : isEdit ? (
              'Save Changes'
            ) : (
              'Create Skill'
            )}
          </button>
        </div>
      </form>
    </div>
  );
}
