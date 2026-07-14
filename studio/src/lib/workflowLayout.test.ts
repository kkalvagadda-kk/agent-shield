import { describe, it, expect } from "vitest";
import { computeWorkflowLayout } from "./workflowLayout";

const members = (...ids: string[]) => ids.map((id, i) => ({ agent_id: id, position: i + 1 }));
const edge = (s: string, t: string) => ({ source_agent_id: s, target_agent_id: t });

describe("computeWorkflowLayout", () => {
  it("fans a conditional fork's targets across rows in the next column", () => {
    // router -> payout | supervisor | confirm  (the 3-way fork)
    const pos = computeWorkflowLayout(
      members("router", "payout", "supervisor", "confirm"),
      [edge("router", "payout"), edge("router", "supervisor"), edge("router", "confirm")],
    );
    // Router sits alone in the first column; all three targets share the next column.
    expect(pos.router.x).toBeLessThan(pos.payout.x);
    expect(pos.payout.x).toBe(pos.supervisor.x);
    expect(pos.supervisor.x).toBe(pos.confirm.x);
    // The three targets are on DISTINCT rows (the bug was all-same-row → looked linear).
    const ys = [pos.payout.y, pos.supervisor.y, pos.confirm.y];
    expect(new Set(ys).size).toBe(3);
  });

  it("lays a sequential chain out as one node per column, same row", () => {
    const pos = computeWorkflowLayout(
      members("a", "b", "c"),
      [edge("a", "b"), edge("b", "c")],
    );
    expect(pos.a.x).toBeLessThan(pos.b.x);
    expect(pos.b.x).toBeLessThan(pos.c.x);
    expect(pos.a.y).toBe(pos.b.y);
    expect(pos.b.y).toBe(pos.c.y);
  });

  it("is cycle-safe (handoff graphs may loop) and never throws / hangs", () => {
    const pos = computeWorkflowLayout(
      members("a", "b", "c"),
      [edge("a", "b"), edge("b", "c"), edge("c", "a")],
    );
    expect(Object.keys(pos)).toHaveLength(3);
    for (const id of ["a", "b", "c"]) {
      expect(Number.isFinite(pos[id].x)).toBe(true);
      expect(Number.isFinite(pos[id].y)).toBe(true);
    }
  });

  it("ignores edges whose endpoints are not members; falls back gracefully", () => {
    const pos = computeWorkflowLayout(members("a", "b"), [edge("a", "ghost"), edge("a", "b")]);
    expect(pos.a.x).toBeLessThan(pos.b.x);
    expect(pos.ghost).toBeUndefined();
  });

  it("returns empty for no members", () => {
    expect(computeWorkflowLayout([], [])).toEqual({});
  });
});
