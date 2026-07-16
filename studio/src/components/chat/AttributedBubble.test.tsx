import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import AttributedBubble from "./AttributedBubble";
import { agentColor } from "../../lib/agentColor";

// A single-agent surface renders unlabeled (one speaker → no name); a
// multi-agent workflow labels each bubble with the member's name + its
// deterministic color dot. The children slot must keep working so surfaces can
// still pass chips / feedback / safety details.
describe("AttributedBubble", () => {
  it("renders no author label when showLabel is false (single-agent surface)", () => {
    renderWithProviders(
      <AttributedBubble role="assistant" author="refund-agent" content="Hi" showLabel={false} />
    );
    expect(screen.queryByText("refund-agent")).not.toBeInTheDocument();
    expect(screen.getByText("Hi")).toBeInTheDocument();
  });

  it("renders no author label when there is no author", () => {
    renderWithProviders(<AttributedBubble role="assistant" content="Hello" />);
    expect(screen.getByText("Hello")).toBeInTheDocument();
    // No label element rendered.
    expect(screen.queryByText(/-agent$/)).not.toBeInTheDocument();
  });

  it("renders the agent name + color dot for a multi-author bubble", () => {
    const { container } = renderWithProviders(
      <AttributedBubble role="assistant" author="fraud-checker" content="Checking" />
    );
    expect(screen.getByText("fraud-checker")).toBeInTheDocument();
    // The colored dot carries the deterministic palette dot class.
    const dotClass = agentColor("fraud-checker").dot;
    expect(container.querySelector(`.${dotClass}`)).not.toBeNull();
  });

  it("applies user vs assistant styling", () => {
    const { container: userC } = renderWithProviders(
      <AttributedBubble role="user" content="ask" />
    );
    // User bubble is right-aligned + blue.
    expect(userC.querySelector(".justify-end")).not.toBeNull();
    expect(userC.querySelector(".bg-blue-600")).not.toBeNull();

    const { container: asstC } = renderWithProviders(
      <AttributedBubble role="assistant" content="answer" />
    );
    expect(asstC.querySelector(".justify-start")).not.toBeNull();
    expect(asstC.querySelector(".bg-slate-100")).not.toBeNull();
  });

  it("renders the children slot (chips / feedback)", () => {
    renderWithProviders(
      <AttributedBubble role="assistant" author="refund-agent" content="Done">
        <span data-testid="chip">tool: refund</span>
      </AttributedBubble>
    );
    expect(screen.getByTestId("chip")).toHaveTextContent("tool: refund");
  });

  it("shows a streaming caret only when streaming", () => {
    const { container } = renderWithProviders(
      <AttributedBubble role="assistant" content="typing" streaming />
    );
    expect(container.querySelector(".animate-pulse")).not.toBeNull();
  });
});
