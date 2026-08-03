import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RunSparkline from "./RunSparkline";

describe("RunSparkline", () => {
  it("distinguishes never-run from all-failed", () => {
    // The distinction the component exists for. An empty strip rendered as ten grey
    // bars would read as "ten bad runs" — a newly armed schedule looking broken.
    const { unmount } = render(<RunSparkline statuses={[]} />);
    expect(screen.getByTestId("run-sparkline-empty")).toHaveTextContent(/no runs yet/i);
    expect(screen.queryByTestId("run-sparkline")).not.toBeInTheDocument();
    unmount();

    render(<RunSparkline statuses={Array(10).fill("failed")} />);
    expect(screen.getByTestId("run-sparkline")).toHaveAttribute("data-failed", "10");
  });

  it("renders one bar per run", () => {
    render(<RunSparkline statuses={["completed", "failed", "completed"]} />);
    const strip = screen.getByTestId("run-sparkline");
    expect(strip).toHaveAttribute("data-count", "3");
    expect(strip.querySelectorAll("span")).toHaveLength(3);
  });

  it("renders oldest → newest even though the server sends newest first", () => {
    // Time reads left-to-right everywhere else in the product; a strip that ran
    // backwards would make a recovering schedule look like a failing one.
    render(<RunSparkline statuses={["completed", "failed", "failed"]} />);
    const title = screen.getByTestId("run-sparkline").getAttribute("title")!;
    expect(title).toContain("oldest first: failed, failed, succeeded");
  });

  it("counts only failures in data-failed, so flaky is distinguishable from broken", () => {
    render(<RunSparkline statuses={["completed", "failed", "completed", "completed"]} />);
    expect(screen.getByTestId("run-sparkline")).toHaveAttribute("data-failed", "1");
  });

  it("falls back to a neutral bar for a status it does not know", () => {
    // A new run status must not crash the page or masquerade as success.
    render(<RunSparkline statuses={["some_new_status"]} />);
    const bar = screen.getByTestId("run-sparkline").querySelector("span")!;
    expect(bar.className).toContain("bg-slate-200");
    expect(screen.getByTestId("run-sparkline").getAttribute("title")).toContain("some_new_status");
  });
});
