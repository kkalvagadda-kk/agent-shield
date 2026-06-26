import { Bot, Circle, Rocket, Save, Wrench, Flag } from 'lucide-react';
import { useWorkflowStore } from '../stores/workflowStore';

interface ToolbarProps {
  onAddNode: (type: 'agent' | 'http_tool' | 'end') => void;
  onSave: () => void;
  onDeploy: () => void;
  isSaving?: boolean;
  isDeploying?: boolean;
}

export default function Toolbar({
  onAddNode,
  onSave,
  onDeploy,
  isSaving = false,
  isDeploying = false,
}: ToolbarProps) {
  const { isDirty, workflowId, workflowName } = useWorkflowStore();
  const canDeploy = !isDirty && !!workflowId;

  return (
    <div className="h-12 border-b border-slate-200 bg-white flex items-center px-4 gap-2 shrink-0">
      {/* Node type buttons */}
      <button
        onClick={() => onAddNode('agent')}
        className="btn-secondary py-1.5 text-xs"
        title="Add Agent node"
      >
        <Bot size={13} />
        + Agent
      </button>

      <button
        onClick={() => onAddNode('http_tool')}
        className="btn-secondary py-1.5 text-xs"
        title="Add HTTP Tool node"
      >
        <Wrench size={13} />
        + HTTP Tool
      </button>

      <button
        onClick={() => onAddNode('end')}
        className="btn-secondary py-1.5 text-xs"
        title="Add End node"
      >
        <Flag size={13} />
        + End
      </button>

      {/* Separator */}
      <div className="h-6 w-px bg-slate-200 mx-1" />

      {/* Workflow name */}
      {workflowName && (
        <span className="text-sm text-slate-600 font-medium mr-1">{workflowName}</span>
      )}

      {/* Dirty indicator */}
      {isDirty && (
        <span className="flex items-center gap-1 text-xs text-amber-600">
          <Circle size={8} className="fill-amber-500 stroke-amber-500" />
          Unsaved changes
        </span>
      )}

      {/* Push buttons to the right */}
      <div className="flex-1" />

      {/* Save */}
      <button
        onClick={onSave}
        disabled={isSaving}
        className="btn-secondary py-1.5 text-xs"
        title={workflowId ? 'Save workflow' : 'Save workflow (first save)'}
      >
        <Save size={13} />
        {isSaving ? 'Saving…' : 'Save'}
      </button>

      {/* Deploy */}
      <button
        onClick={onDeploy}
        disabled={!canDeploy || isDeploying}
        className="btn-primary py-1.5 text-xs"
        title={
          !workflowId
            ? 'Save the workflow before deploying'
            : isDirty
            ? 'Save your changes before deploying'
            : 'Deploy workflow'
        }
      >
        <Rocket size={13} />
        {isDeploying ? 'Deploying…' : 'Deploy'}
      </button>
    </div>
  );
}
