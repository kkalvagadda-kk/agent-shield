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

  // --- POC-2b rich slots ---

  // The single-agent degenerate case (no author + no rich props) must render
  // exactly as before: no label, no avatar, no chip, no rationale/citation row.
  it("degenerate single-agent bubble renders no attribution or rich slots (DOM parity)", () => {
    const { container } = renderWithProviders(
      <AttributedBubble role="assistant" content="Just an answer" showLabel={false} />
    );
    // No label header (dot/name), no avatar icon, no amber/tool/citation rows.
    expect(container.querySelector("svg")).toBeNull(); // no Bot/Lightbulb/Database
    expect(container.querySelector(".bg-amber-50")).toBeNull();
    expect(container.querySelector(".animate-pulse")).toBeNull();
    // Just the content box (bg-slate-100) inside the max-w wrapper.
    const outer = container.firstElementChild as HTMLElement;
    expect(outer.querySelectorAll("div").length).toBe(2); // max-w wrapper + content box
    expect(screen.getByText("Just an answer")).toBeInTheDocument();
  });

  it("renders a tinted Bot avatar next to the name when avatar is set", () => {
    const { container } = renderWithProviders(
      <AttributedBubble role="assistant" author="researcher" content="hi" avatar />
    );
    expect(screen.getByText("researcher")).toBeInTheDocument();
    // The Bot icon carries the agent's deterministic text-color class.
    const textClass = agentColor("researcher").text;
    expect(container.querySelector(`svg.${textClass}`)).not.toBeNull();
  });

  it("renders a ToolCallChip for each toolCall", () => {
    renderWithProviders(
      <AttributedBubble
        role="assistant"
        author="researcher"
        content="done"
        toolCalls={[{ tool_name: "get_weather", status: "ok" }]}
      />
    );
    expect(screen.getByText("get_weather")).toBeInTheDocument();
  });

  it("renders the amber rationale box when rationale is set and showRationale is true", () => {
    const { container } = renderWithProviders(
      <AttributedBubble
        role="assistant"
        author="researcher"
        content="done"
        rationale="Looking up the forecast first"
        showRationale
      />
    );
    expect(container.querySelector(".bg-amber-50")).not.toBeNull();
    expect(screen.getByText(/Looking up the forecast first/)).toBeInTheDocument();
  });

  it("hides the rationale box when showRationale is false", () => {
    const { container } = renderWithProviders(
      <AttributedBubble
        role="assistant"
        author="researcher"
        content="done"
        rationale="hidden reasoning"
        showRationale={false}
      />
    );
    expect(container.querySelector(".bg-amber-50")).toBeNull();
    expect(screen.queryByText(/hidden reasoning/)).not.toBeInTheDocument();
  });

  it("renders no citation chip row when citations is empty (POC-2b slot only)", () => {
    const { container } = renderWithProviders(
      <AttributedBubble role="assistant" author="researcher" content="done" citations={[]} />
    );
    // No Database citation glyph / chip row for an empty citations array.
    expect(container.querySelector("code")).toBeNull();
  });
});
