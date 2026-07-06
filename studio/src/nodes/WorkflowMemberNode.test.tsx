import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import { renderWithProviders } from "../test/utils";
import { WorkflowMemberNode, type WorkflowMemberNodeData } from "./WorkflowMemberNode";
import type { NodeProps, Node } from "@xyflow/react";

type WFMNodeType = Node<WorkflowMemberNodeData, "workflow_member">;

function makeProps(
  data: WorkflowMemberNodeData,
  selected = false
): NodeProps<WFMNodeType> {
  return {
    id: "node-1",
    type: "workflow_member",
    data,
    selected,
    dragging: false,
    isConnectable: true,
    zIndex: 1,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
  } as NodeProps<WFMNodeType>;
}

describe("WorkflowMemberNode", () => {
  it("renders the agent name", () => {
    renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot" })}
        />
      </ReactFlowProvider>
    );
    expect(screen.getByText("alpha-bot")).toBeInTheDocument();
  });

  it("shows the numeric position badge", () => {
    renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot", position: 1 })}
        />
      </ReactFlowProvider>
    );
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("hides position badge when position is undefined", () => {
    const { container } = renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "no-pos-bot" })}
        />
      </ReactFlowProvider>
    );
    // The only numbers in the DOM should not come from a position badge
    const spans = container.querySelectorAll("span");
    const numericSpans = Array.from(spans).filter(
      (s) => s.textContent === "1" || s.textContent === "0"
    );
    expect(numericSpans).toHaveLength(0);
  });

  it("shows the role chip", () => {
    renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot", role: "orchestrator" })}
        />
      </ReactFlowProvider>
    );
    expect(screen.getByText("orchestrator")).toBeInTheDocument();
  });

  it("hides role chip when role is not set", () => {
    renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot" })}
        />
      </ReactFlowProvider>
    );
    expect(screen.queryByText("orchestrator")).not.toBeInTheDocument();
  });

  it("applies selected (blue) border when selected=true", () => {
    const { container } = renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot" }, true)}
        />
      </ReactFlowProvider>
    );
    expect(container.querySelector(".border-blue-500")).toBeInTheDocument();
  });

  it("applies default (slate) border when not selected", () => {
    const { container } = renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "alpha-bot" }, false)}
        />
      </ReactFlowProvider>
    );
    expect(container.querySelector(".border-slate-200")).toBeInTheDocument();
  });

  it("falls back to 'Agent' when agent_name is empty", () => {
    renderWithProviders(
      <ReactFlowProvider>
        <WorkflowMemberNode
          {...makeProps({ agent_id: "a1", agent_name: "" })}
        />
      </ReactFlowProvider>
    );
    expect(screen.getByText("Agent")).toBeInTheDocument();
  });
});
