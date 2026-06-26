import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { toast } from 'sonner';
import { X } from 'lucide-react';
import { listTeams, createWorkflow } from '../api/registryApi';
import { useWorkflowStore } from '../stores/workflowStore';
import { serializeWorkflow } from '../utils/workflowSerializer';

interface FirstSaveModalProps {
  onClose: () => void;
  onSaved: (id: string, name: string, team: string) => void;
}

const NAME_PATTERN = /^[a-z0-9-]+$/;

export default function FirstSaveModal({ onClose, onSaved }: FirstSaveModalProps) {
  const [name, setName] = useState('');
  const [team, setTeam] = useState('');
  const [description, setDescription] = useState('');
  const [nameError, setNameError] = useState('');
  const [isSaving, setIsSaving] = useState(false);

  const { nodes, edges } = useWorkflowStore();

  const { data: teamsData } = useQuery({
    queryKey: ['teams'],
    queryFn: listTeams,
  });

  const validateName = (value: string) => {
    if (!value) return 'Workflow name is required';
    if (!NAME_PATTERN.test(value))
      return 'Only lowercase letters, numbers, and hyphens allowed';
    return '';
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const err = validateName(name);
    if (err) {
      setNameError(err);
      return;
    }
    if (!team) {
      toast.error('Please select a team');
      return;
    }

    setIsSaving(true);
    try {
      const definition = serializeWorkflow(nodes, edges);
      const workflow = await createWorkflow({
        name,
        team,
        description: description || undefined,
        definition,
      });
      toast.success(`Workflow "${workflow.name}" saved`);
      onSaved(workflow.id, workflow.name, workflow.team);
    } catch (err) {
      toast.error(`Failed to save: ${String(err)}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 bg-black/40 flex items-center justify-center z-50"
      onClick={(e) => e.target === e.currentTarget && onClose()}
    >
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-slate-900">Save Workflow</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 transition-colors"
          >
            <X size={18} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Name */}
          <div>
            <label className="label" htmlFor="wf-name">
              Workflow Name <span className="text-red-500">*</span>
            </label>
            <input
              id="wf-name"
              className="input"
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                setNameError(validateName(e.target.value));
              }}
              placeholder="my-workflow"
            />
            {nameError && (
              <p className="mt-1 text-xs text-red-600">{nameError}</p>
            )}
            <p className="mt-1 text-xs text-slate-400">
              Lowercase letters, numbers, and hyphens only
            </p>
          </div>

          {/* Team */}
          <div>
            <label className="label" htmlFor="wf-team">
              Team <span className="text-red-500">*</span>
            </label>
            <select
              id="wf-team"
              className="input"
              value={team}
              onChange={(e) => setTeam(e.target.value)}
            >
              <option value="">Select a team…</option>
              {teamsData?.items.map((t) => (
                <option key={t.id} value={t.name}>
                  {t.name}
                </option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="label" htmlFor="wf-desc">
              Description <span className="text-slate-400 font-normal">(optional)</span>
            </label>
            <textarea
              id="wf-desc"
              className="input resize-none"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this workflow do?"
            />
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn-secondary">
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSaving || !!nameError}
              className="btn-primary"
            >
              {isSaving ? 'Saving…' : 'Save Workflow'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
