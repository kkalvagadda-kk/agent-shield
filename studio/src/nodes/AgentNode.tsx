import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Bot } from 'lucide-react';

export type AgentConfig = {
  name: string;
  instructions: string;
  model: string;
  risk: string;
  tool_ids?: string[];
  skill_ids?: string[];
};

export type AgentNodeData = {
  config: AgentConfig;
  [key: string]: unknown;
};

type AgentNodeType = Node<AgentNodeData, 'agent'>;

export const AgentNode = memo(({ data, selected }: NodeProps<AgentNodeType>) => {
  const config = data.config;
  const toolCount = config?.tool_ids?.length ?? 0;
  const skillCount = config?.skill_ids?.length ?? 0;

  return (
    <div
      className={`rounded-lg border-2 bg-white p-3 min-w-[160px] shadow-sm transition-colors ${
        selected ? 'border-blue-500' : 'border-slate-200'
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center gap-2">
        <Bot size={16} className="text-blue-500 shrink-0" />
        <span className="text-sm font-medium text-slate-800 truncate">
          {config?.name || 'Agent'}
        </span>
      </div>
      {(toolCount > 0 || skillCount > 0) && (
        <div className="mt-1 flex gap-1">
          {toolCount > 0 && (
            <span className="text-[10px] bg-blue-50 text-blue-600 px-1.5 rounded-full">
              {toolCount} tool{toolCount > 1 ? 's' : ''}
            </span>
          )}
          {skillCount > 0 && (
            <span className="text-[10px] bg-purple-50 text-purple-600 px-1.5 rounded-full">
              {skillCount} skill{skillCount > 1 ? 's' : ''}
            </span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

AgentNode.displayName = 'AgentNode';
