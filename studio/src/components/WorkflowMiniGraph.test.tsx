import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import WorkflowMiniGraph from "./WorkflowMiniGraph";
import type { WorkflowMember, WorkflowEdge } from "../api/registryApi";

const NOW = new Date().toISOString();

const members: WorkflowMember[] = [
  { workflow_id: "w1", agent_id: "a1", agent_name: "planner", role: null, position: 0, routing: {}, added_at: NOW },
  { workflow_id: "w1", agent_id: "a2", agent_name: "executor", role: null, position: 1, routing: {}, added_at: NOW },
];

const edges: WorkflowEdge[] = [
  { id: "e1", workflow_id: "w1", source_agent_id: "a1", target_agent_id: "a2", condition: null, position: 0, created_at: NOW },
];

describe("WorkflowMiniGraph", () => {
  it("renders nodes for each member", () => {
    const { container } = render(<WorkflowMiniGraph members={members} edges={edges} />);
    const texts = container.querySelectorAll("text");
    const labels = Array.from(texts).map((t) => t.textContent);
    expect(labels).toContain("planner");
    expect(labels).toContain("executor");
  });

  it("renders an edge line", () => {
    const { container } = render(<WorkflowMiniGraph members={members} edges={edges} />);
    const lines = container.querySelectorAll("line");
    expect(lines.length).toBe(1);
  });

  it("shows empty state when no members", () => {
    render(<WorkflowMiniGraph members={[]} edges={[]} />);
    expect(screen.getByText(/no members/i)).toBeInTheDocument();
  });

  it("truncates long agent names", () => {
    const longMember: WorkflowMember[] = [
      { workflow_id: "w1", agent_id: "a1", agent_name: "super-long-agent-name-here", role: null, position: 0, routing: {}, added_at: NOW },
    ];
    const { container } = render(<WorkflowMiniGraph members={longMember} edges={[]} />);
    const text = container.querySelector("text");
    expect(text?.textContent?.endsWith("…")).toBe(true);
  });
});
