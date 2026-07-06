import { describe, it, expect, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderWithProviders } from "../../test/utils";
import TracePanel, { type TraceEvent } from "./TracePanel";

const EVENTS: TraceEvent[] = [
  { ts: "2024-01-01T12:00:00.000Z", event: "text_delta", content: "Hello from the agent" },
  { ts: "2024-01-01T12:00:01.000Z", event: "tool_call_start", tool_name: "search_web" },
  { ts: "2024-01-01T12:00:02.000Z", event: "tool_call_end", tool_name: "search_web", result: "found 10 results" },
  { ts: "2024-01-01T12:00:03.000Z", event: "done" },
];

describe("TracePanel", () => {
  it("shows 'No events yet' when events list is empty and not collapsed", () => {
    renderWithProviders(
      <TracePanel events={[]} collapsed={false} onToggle={vi.fn()} />
    );
    expect(screen.getByText(/no events yet/i)).toBeInTheDocument();
  });

  it("hides event content when collapsed", () => {
    renderWithProviders(
      <TracePanel events={EVENTS} collapsed={true} onToggle={vi.fn()} />
    );
    // Event content should not be visible when collapsed
    expect(screen.queryByText(/no events yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/text_delta/)).not.toBeInTheDocument();
  });

  it("shows 'Event Trace' label when not collapsed", () => {
    renderWithProviders(
      <TracePanel events={[]} collapsed={false} onToggle={vi.fn()} />
    );
    expect(screen.getByText("Event Trace")).toBeInTheDocument();
  });

  it("does not show 'Event Trace' label text when collapsed", () => {
    renderWithProviders(
      <TracePanel events={[]} collapsed={true} onToggle={vi.fn()} />
    );
    expect(screen.queryByText("Event Trace")).not.toBeInTheDocument();
  });

  it("renders event types in the trace log", () => {
    renderWithProviders(
      <TracePanel events={EVENTS} collapsed={false} onToggle={vi.fn()} />
    );
    expect(screen.getByText(/\[text_delta\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[tool_call_start\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[tool_call_end\]/)).toBeInTheDocument();
    expect(screen.getByText(/\[done\]/)).toBeInTheDocument();
  });

  it("renders tool names for tool_call events", () => {
    renderWithProviders(
      <TracePanel events={EVENTS} collapsed={false} onToggle={vi.fn()} />
    );
    const toolNames = screen.getAllByText("search_web");
    expect(toolNames.length).toBeGreaterThanOrEqual(1);
  });

  it("renders content snippet for text_delta events", () => {
    renderWithProviders(
      <TracePanel events={EVENTS} collapsed={false} onToggle={vi.fn()} />
    );
    expect(screen.getByText(/hello from the agent/i)).toBeInTheDocument();
  });

  it("shows the result for tool_call_end events", () => {
    renderWithProviders(
      <TracePanel events={EVENTS} collapsed={false} onToggle={vi.fn()} />
    );
    expect(screen.getByText(/found 10 results/i)).toBeInTheDocument();
  });

  it("fires onToggle when the toggle button is clicked", async () => {
    const onToggle = vi.fn();
    renderWithProviders(
      <TracePanel events={[]} collapsed={false} onToggle={onToggle} />
    );
    await userEvent.click(screen.getByRole("button"));
    expect(onToggle).toHaveBeenCalledOnce();
  });

  it("truncates content longer than 60 characters with an ellipsis", () => {
    const longContent = "A".repeat(70);
    renderWithProviders(
      <TracePanel
        events={[{ ts: "2024-01-01T12:00:00.000Z", event: "text_delta", content: longContent }]}
        collapsed={false}
        onToggle={vi.fn()}
      />
    );
    // The content should be truncated
    expect(screen.getByText(/A{60}…/)).toBeInTheDocument();
  });
});
