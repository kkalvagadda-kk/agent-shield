import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Flag } from 'lucide-react';

export type EndConfig = {
  output_mapping: Record<string, string>;
};

export type EndNodeData = {
  config: EndConfig;
  [key: string]: unknown;
};

type EndNodeType = Node<EndNodeData, 'end'>;

export const EndNode = memo(({ selected }: NodeProps<EndNodeType>) => {
  return (
    <div
      className={`rounded-full border-2 bg-amber-50 px-4 py-2 shadow-sm flex items-center gap-2 transition-colors ${
        selected ? 'border-amber-500' : 'border-amber-300'
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <Flag size={14} className="text-amber-600 shrink-0" />
      <span className="text-sm font-medium text-amber-800">End</span>
    </div>
  );
});

EndNode.displayName = 'EndNode';
