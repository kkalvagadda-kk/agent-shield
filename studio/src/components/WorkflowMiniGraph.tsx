interface MiniMember {
  agent_id: string;
  agent_name: string | null;
}

interface MiniEdge {
  id?: string;
  source_agent_id: string;
  target_agent_id: string;
}

interface Props {
  members: MiniMember[];
  edges: MiniEdge[];
  className?: string;
}

interface NodePos {
  id: string;
  label: string;
  x: number;
  y: number;
}

export default function WorkflowMiniGraph({ members, edges, className = "" }: Props) {
  if (members.length === 0) {
    return (
      <div className={`rounded-lg border border-slate-200 p-4 text-center text-sm text-slate-400 ${className}`}>
        No members in this workflow.
      </div>
    );
  }

  const NODE_W = 120;
  const NODE_H = 36;
  const GAP_X = 60;
  const GAP_Y = 70;
  const PAD = 20;

  const targetIds = new Set(edges.map((e) => e.target_agent_id));
  const sourceIds = new Set(edges.map((e) => e.source_agent_id));

  const roots = members.filter((m) => !targetIds.has(m.agent_id));
  if (roots.length === 0 && members.length > 0) roots.push(members[0]);

  const placed = new Map<string, NodePos>();
  const queue = [...roots];
  let col = 0;

  while (queue.length > 0) {
    const batch = [...queue];
    queue.length = 0;
    batch.forEach((m, row) => {
      if (placed.has(m.agent_id)) return;
      placed.set(m.agent_id, {
        id: m.agent_id,
        label: m.agent_name ?? m.agent_id.slice(0, 8),
        x: PAD + col * (NODE_W + GAP_X),
        y: PAD + row * (NODE_H + GAP_Y),
      });
      const children = edges
        .filter((e) => e.source_agent_id === m.agent_id)
        .map((e) => members.find((mm) => mm.agent_id === e.target_agent_id))
        .filter(Boolean) as MiniMember[];
      children.forEach((ch) => { if (!placed.has(ch.agent_id)) queue.push(ch); });
    });
    col++;
  }

  members.forEach((m, i) => {
    if (!placed.has(m.agent_id)) {
      placed.set(m.agent_id, {
        id: m.agent_id,
        label: m.agent_name ?? m.agent_id.slice(0, 8),
        x: PAD + col * (NODE_W + GAP_X),
        y: PAD + i * (NODE_H + GAP_Y),
      });
    }
  });

  const nodes = Array.from(placed.values());
  const svgW = Math.max(...nodes.map((n) => n.x + NODE_W)) + PAD;
  const svgH = Math.max(...nodes.map((n) => n.y + NODE_H)) + PAD;

  return (
    <div className={`rounded-lg border border-slate-200 overflow-auto bg-slate-50 ${className}`}>
      <svg width={svgW} height={svgH} className="min-w-full">
        <defs>
          <marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto">
            <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
          </marker>
        </defs>
        {edges.map((e, i) => {
          const src = placed.get(e.source_agent_id);
          const tgt = placed.get(e.target_agent_id);
          if (!src || !tgt) return null;
          const x1 = src.x + NODE_W;
          const y1 = src.y + NODE_H / 2;
          const x2 = tgt.x;
          const y2 = tgt.y + NODE_H / 2;
          return (
            <line
              key={e.id ?? `edge-${i}`}
              x1={x1} y1={y1} x2={x2} y2={y2}
              stroke="#94a3b8"
              strokeWidth={1.5}
              markerEnd="url(#arrowhead)"
            />
          );
        })}
        {nodes.map((n) => (
          <g key={n.id}>
            <rect
              x={n.x} y={n.y}
              width={NODE_W} height={NODE_H}
              rx={6}
              fill="white"
              stroke="#cbd5e1"
              strokeWidth={1}
            />
            <text
              x={n.x + NODE_W / 2}
              y={n.y + NODE_H / 2 + 4}
              textAnchor="middle"
              className="text-[11px] fill-slate-700"
              fontFamily="monospace"
            >
              {n.label.length > 14 ? n.label.slice(0, 12) + "…" : n.label}
            </text>
          </g>
        ))}
      </svg>
    </div>
  );
}
