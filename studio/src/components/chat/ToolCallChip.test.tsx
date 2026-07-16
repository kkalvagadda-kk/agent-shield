import { describe, it, expect } from "vitest";
import { screen } from "@testing-library/react";
import { renderWithProviders } from "../../test/utils";
import ToolCallChip from "./ToolCallChip";

// The chip is the visible proof a member invoked a governed tool (POC-2b 2b-i).
// It renders the tool name, and a failed call reads differently (red tint).
describe("ToolCallChip", () => {
  it("renders the tool name", () => {
    renderWithProviders(<ToolCallChip tool="get_weather" />);
    expect(screen.getByText("get_weather")).toBeInTheDocument();
  });

  it("uses the muted slate tint for an ok call", () => {
    const { container } = renderWithProviders(<ToolCallChip tool="lookup" status="ok" />);
    expect(container.querySelector(".text-slate-500")).not.toBeNull();
    expect(container.querySelector(".text-red-600")).toBeNull();
  });

  it("tints red when the tool call errored", () => {
    const { container } = renderWithProviders(
      <ToolCallChip tool="lookup" status="error" />,
    );
    expect(container.querySelector(".text-red-600")).not.toBeNull();
  });
});
