import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Bot } from 'lucide-react';

export type AgentConfig = {
  name: string;
  instructions: string;
  model: string;
  risk: string;
};

export type AgentNodeData = {
  config: AgentConfig;
  [key: string]: unknown;
};

type AgentNodeType = Node<AgentNodeData, 'agent'>;

export const AgentNode = memo(({ data, selected }: NodeProps<AgentNodeType>) => {
  const config = data.config;
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
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

AgentNode.displayName = 'AgentNode';
