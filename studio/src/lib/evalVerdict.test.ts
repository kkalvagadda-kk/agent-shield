import { describe, it, expect } from "vitest";
import {
  verdictOf,
  scoreColor,
  passesGate,
  thresholdLabel,
  formatDelta,
} from "./evalVerdict";

// THE regression this module exists for.
//
// The verdict rule used to live in four places across three services, each
// defaulting to 0.7. They agreed, so nothing ever errored — until per-run
// thresholds shipped and the copies diverged: a 0.85 run on a 0.9-threshold
// dataset rendered GREEN in the Studio while the publish gate refused it. The UI
// contradicted the product it reports on.
//
// So the load-bearing test is not "0.85 passes" — it is that the SAME score
// yields DIFFERENT verdicts under different thresholds. One fixture, two
// thresholds. If this file ever passes with a hardcoded literal in the module,
// the literal is the bug.
describe("verdictOf — the same score, two thresholds", () => {
  it("passes 0.85 at threshold 0.7 and does NOT pass it at 0.9", () => {
    // The load-bearing assertion is the GATE, not the band: the same score must
    // publish under one threshold and be refused under the other. 0.85 against 0.9
    // lands in the amber "near" band (>= 0.6x the threshold but below it) — that is
    // the donor's rule from EvalResultsPage and it must not drift.
    expect(verdictOf(0.85, 0.7)).toBe("pass");
    expect(verdictOf(0.85, 0.9)).toBe("near");

    expect(passesGate(0.85, 0.7)).toBe(true);
    expect(passesGate(0.85, 0.9)).toBe(false);
  });

  it("treats score === threshold as a pass (inclusive boundary)", () => {
    // Must match the server: routers/eval_runner.py gates on
    // `overall_score >= effective_pass_threshold`. An exclusive boundary here
    // would render "failed" for a run the gate actually publishes.
    expect(verdictOf(0.9, 0.9)).toBe("pass");
    expect(passesGate(0.9, 0.9)).toBe(true);
  });
});

// Fail-closed. An absent threshold must NEVER read as a pass — not even for a
// perfect score. A local default here would re-declare the threshold, which is
// precisely how it came to exist four times.
describe("verdictOf — fail-closed when inputs are missing", () => {
  it("returns 'unknown', never 'pass', when the threshold is absent", () => {
    expect(verdictOf(0.99, null)).toBe("unknown");
    expect(verdictOf(0.99, undefined)).toBe("unknown");
    expect(passesGate(0.99, null)).toBe(false);
  });

  it("returns 'unknown' when the score is absent", () => {
    expect(verdictOf(null, 0.7)).toBe("unknown");
    expect(verdictOf(undefined, 0.7)).toBe("unknown");
    expect(passesGate(null, 0.7)).toBe(false);
  });
});

// The amber band is "close, but the gate still says no". Its edge is a fraction
// of the run's OWN threshold, never a literal — the same rule the donor
// implementation in EvalResultsPage used.
describe("verdictOf — the near band", () => {
  it("is 'near' between 0.6x the threshold and the threshold itself", () => {
    expect(verdictOf(0.63, 0.9)).toBe("near"); // 0.6 * 0.9 = 0.54
    expect(verdictOf(0.89, 0.9)).toBe("near");
  });

  it("is 'fail' below 0.6x the threshold", () => {
    expect(verdictOf(0.53, 0.9)).toBe("fail");
  });

  it("moves the band with the threshold, not with a literal", () => {
    // 0.45 is 'near' under a 0.7 threshold (0.6*0.7 = 0.42) but 'fail' under 0.9.
    expect(verdictOf(0.45, 0.7)).toBe("near");
    expect(verdictOf(0.45, 0.9)).toBe("fail");
  });
});

describe("scoreColor", () => {
  it("returns the green band for a passing score", () => {
    expect(scoreColor(0.95, 0.9)).toContain("green");
  });

  it("returns the amber band for the same score under a stricter threshold", () => {
    expect(scoreColor(0.85, 0.7)).toContain("green");
    expect(scoreColor(0.85, 0.9)).toContain("amber");
  });

  it("returns the neutral band (no colour) when the threshold is absent", () => {
    expect(scoreColor(0.99, null)).toBe("");
  });

  it("returns the neutral band when the score is absent", () => {
    expect(scoreColor(null, 0.9)).toBe("");
  });
});

// The reason a human can see WHY a good-looking score did not publish.
describe("thresholdLabel", () => {
  it("renders score and the bar it had to clear", () => {
    expect(thresholdLabel(0.85, 0.9)).toBe("0.85 / needs 0.90");
  });

  it("renders the score alone when there is no threshold to compare against", () => {
    expect(thresholdLabel(0.85, null)).toBe("0.85");
  });

  it("renders an em dash when there is no score", () => {
    expect(thresholdLabel(null, 0.9)).toBe("—");
  });
});

describe("formatDelta", () => {
  it("signs an improvement", () => {
    expect(formatDelta(0.9, 0.8)).toBe("+0.10");
  });

  it("signs a regression", () => {
    expect(formatDelta(0.8, 0.9)).toBe("-0.10");
  });

  it("renders an em dash when either side is missing", () => {
    expect(formatDelta(0.9, null)).toBe("—");
    expect(formatDelta(null, 0.9)).toBe("—");
  });
});
