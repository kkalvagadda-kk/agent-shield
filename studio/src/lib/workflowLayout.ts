// ---------------------------------------------------------------------------
// workflowLayout.ts — layered auto-layout for the composite Workflow builder.
//
// The builder loads a persisted workflow (members + edges) and must POSITION the
// member nodes on the canvas. A naive single-row layout (every node at the same
// y, x by member position) cannot represent a *fork*: when one node has several
// outgoing edges (a conditional router → payout / supervisor / confirm), the
// edges overlap the node row and read as a misleading linear chain.
//
// This computes a left-to-right layered layout keyed on graph STRUCTURE, not the
// member index: column = longest-path depth from a root (indegree-0) node, and
// rows within a column are spread vertically so branch targets are visually
// distinct. A plain sequential chain still lays out as one node per column
// (unchanged look); a fork fans its targets across rows in the next column.
// Cycle-safe (handoff graphs may loop) — depth is clamped to node count.
// ---------------------------------------------------------------------------

export interface LayoutMember {
  agent_id: string;
  position?: number | null;
}

export interface LayoutEdge {
  source_agent_id: string;
  target_agent_id: string;
}

export interface XY {
  x: number;
  y: number;
}

const COL_GAP = 280; // horizontal spacing between depth layers
const ROW_GAP = 120; // vertical spacing between siblings in a layer
const X0 = 40;
const Y_CENTER = 200;

/**
 * Position every member by graph depth (column) + sibling order (row).
 * Returns a map of agent_id → {x, y}. Only edges whose endpoints are both
 * members are considered; unknown/orphan edges are ignored.
 */
export function computeWorkflowLayout(
  members: LayoutMember[],
  edges: LayoutEdge[],
): Record<string, XY> {
  const ids = members.map((m) => m.agent_id);
  if (ids.length === 0) return {};

  const idSet = new Set(ids);
  const orderOf = new Map<string, number>(
    members.map((m, i) => [m.agent_id, m.position ?? i + 1]),
  );

  const out = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  ids.forEach((id) => {
    out.set(id, []);
    indeg.set(id, 0);
  });
  for (const e of edges) {
    if (!idSet.has(e.source_agent_id) || !idSet.has(e.target_agent_id)) continue;
    out.get(e.source_agent_id)!.push(e.target_agent_id);
    indeg.set(e.target_agent_id, (indeg.get(e.target_agent_id) ?? 0) + 1);
  }

  // Longest-path depth from the roots (indegree 0), relaxed with re-queue.
  // Depth is clamped to < ids.length so a cycle can't inflate it or loop forever.
  const depth = new Map<string, number>(ids.map((id) => [id, 0]));
  const roots = ids.filter((id) => (indeg.get(id) ?? 0) === 0);
  const queue: string[] = roots.length ? [...roots] : [...ids];
  const maxDepth = ids.length - 1;
  let guard = 0;
  const cap = (ids.length + 1) * (ids.length + 1) + 100;
  while (queue.length && guard < cap) {
    guard++;
    const n = queue.shift()!;
    const d = depth.get(n) ?? 0;
    for (const t of out.get(n) ?? []) {
      const nd = d + 1;
      if (nd <= maxDepth && (depth.get(t) ?? 0) < nd) {
        depth.set(t, nd);
        queue.push(t); // propagate the improvement
      }
    }
  }

  // Group by column, order rows by member position, center each column on Y_CENTER.
  const byCol = new Map<number, string[]>();
  ids.forEach((id) => {
    const c = depth.get(id) ?? 0;
    (byCol.get(c) ?? byCol.set(c, []).get(c)!).push(id);
  });

  const pos: Record<string, XY> = {};
  for (const [col, colIds] of byCol) {
    colIds.sort((a, b) => (orderOf.get(a) ?? 0) - (orderOf.get(b) ?? 0));
    const n = colIds.length;
    colIds.forEach((id, row) => {
      pos[id] = {
        x: col * COL_GAP + X0,
        y: (row - (n - 1) / 2) * ROW_GAP + Y_CENTER,
      };
    });
  }
  return pos;
}
