import { describe, it, expect } from "vitest";
import { agentColor, AGENT_COLOR_PALETTE_SIZE } from "./agentColor";

// A multi-agent workflow only reads as a real multi-speaker conversation if each
// agent keeps the SAME color everywhere. So the color must be a pure, stable
// function of the name — and different names must spread across the palette so
// adjacent speakers are visually distinct.
describe("agentColor", () => {
  it("returns the same color for the same name (stable across calls)", () => {
    expect(agentColor("refund-agent")).toEqual(agentColor("refund-agent"));
    expect(agentColor("fraud-checker")).toEqual(agentColor("fraud-checker"));
  });

  it("spreads different names across the palette", () => {
    const names = [
      "refund-agent",
      "fraud-checker",
      "router",
      "summarizer",
      "planner",
      "researcher",
      "coder",
      "reviewer",
      "dispatcher",
      "validator",
      "escalation",
      "greeter",
    ];
    const dots = new Set(names.map((n) => agentColor(n).dot));
    // Not all collapse into one bucket — expect a healthy spread.
    expect(dots.size).toBeGreaterThan(1);
    expect(dots.size).toBeLessThanOrEqual(AGENT_COLOR_PALETTE_SIZE);
  });

  it("is empty/undefined/null safe (returns a neutral entry)", () => {
    const empty = agentColor("");
    const undef = agentColor(undefined);
    const nul = agentColor(null);
    expect(empty).toEqual(undef);
    expect(undef).toEqual(nul);
    // Neutral entry has fully-formed class strings.
    expect(empty.bg).toBeTruthy();
    expect(empty.text).toBeTruthy();
    expect(empty.border).toBeTruthy();
    expect(empty.dot).toBeTruthy();
  });

  it("returns fully-populated static class strings for a named agent", () => {
    const c = agentColor("refund-agent");
    expect(c.bg).toMatch(/^bg-/);
    expect(c.text).toMatch(/^text-/);
    expect(c.border).toMatch(/^border-/);
    expect(c.dot).toMatch(/^bg-/);
  });
});
