import { memo } from 'react';
import { Handle, Position, type NodeProps, type Node } from '@xyflow/react';
import { Wrench } from 'lucide-react';

export type HttpToolConfig = {
  name: string;
  endpoint: string;
  method: string;
  headers: Record<string, string>;
  body_template: string;
  risk: string;
  auth_config_id: string | null;
};

export type HttpToolNodeData = {
  config: HttpToolConfig;
  [key: string]: unknown;
};

type HttpToolNodeType = Node<HttpToolNodeData, 'http_tool'>;

const METHOD_COLORS: Record<string, string> = {
  GET: 'bg-green-100 text-green-700',
  POST: 'bg-blue-100 text-blue-700',
  PUT: 'bg-amber-100 text-amber-700',
  DELETE: 'bg-red-100 text-red-700',
  PATCH: 'bg-purple-100 text-purple-700',
};

export const HttpToolNode = memo(({ data, selected }: NodeProps<HttpToolNodeType>) => {
  const config = data.config;
  const method = (config?.method ?? 'GET').toUpperCase();
  const methodColor = METHOD_COLORS[method] ?? 'bg-slate-100 text-slate-600';

  // Shorten the endpoint URL for display
  const displayUrl = config?.endpoint
    ? config.endpoint.replace(/^https?:\/\//, '')
    : 'Endpoint not set';

  return (
    <div
      className={`rounded-lg border-2 bg-white p-3 min-w-[180px] shadow-sm transition-colors ${
        selected ? 'border-purple-500' : 'border-slate-200'
      }`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="flex items-center gap-2 mb-1.5">
        <Wrench size={14} className="text-purple-500 shrink-0" />
        <span className="text-sm font-medium text-slate-800 truncate">
          {config?.name || 'HTTP Tool'}
        </span>
      </div>
      <div className="flex items-center gap-1.5">
        <span className={`badge text-[10px] px-1.5 py-0.5 ${methodColor}`}>{method}</span>
        <span className="text-[11px] text-slate-400 truncate max-w-[120px]">{displayUrl}</span>
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

HttpToolNode.displayName = 'HttpToolNode';
