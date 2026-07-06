import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Bot, Plus } from 'lucide-react';

export type WorkflowMemberNodeData = {
  agent_id: string;
  agent_name: string;
  role?: string;
  position?: number;
  is_inline?: boolean;
  inline_dirty?: boolean;
  routing?: Record<string, unknown>;
  [key: string]: unknown;
};

type WorkflowMemberNodeType = Node<WorkflowMemberNodeData, 'workflow_member'>;

export const WorkflowMemberNode = memo(
  ({ data, selected }: NodeProps<WorkflowMemberNodeType>) => {
    const borderCls = selected
      ? 'border-blue-500'
      : data.inline_dirty
        ? 'border-amber-400'
        : 'border-slate-200';
    return (
      <div
        className={`rounded-lg border-2 bg-white p-3 min-w-[180px] shadow-sm transition-colors ${borderCls}`}
      >
        <Handle type="target" position={Position.Left} />

        <div className="flex items-center gap-2">
          {data.position !== undefined && (
            <span className="text-[10px] font-bold bg-slate-100 text-slate-600 rounded-full w-5 h-5 flex items-center justify-center shrink-0">
              {String(data.position)}
            </span>
          )}
          <Bot size={16} className="text-blue-500 shrink-0" />
          <span className="text-sm font-medium text-slate-800 truncate max-w-[130px]">
            {data.agent_name || 'Agent'}
          </span>
          {data.is_inline && (
            <span
              title="Created inline in this workflow"
              className="ml-auto inline-flex items-center gap-0.5 text-[10px] bg-teal-50 text-teal-600 px-1 py-0.5 rounded-full shrink-0"
            >
              <Plus size={9} /> new
            </span>
          )}
        </div>

        {data.role && (
          <div className="mt-1.5 ml-7">
            <span className="text-[10px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded-full">
              {String(data.role)}
            </span>
          </div>
        )}

        <Handle type="source" position={Position.Right} />
      </div>
    );
  },
);

WorkflowMemberNode.displayName = 'WorkflowMemberNode';
