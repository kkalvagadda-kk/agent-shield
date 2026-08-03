import { describe, expect, it } from "vitest";
import { cronHint, describeCron } from "./cron";

// `describeCron` moved out of OverviewScheduled.tsx so the Schedules page could
// render the identical hint. These cases exist so the move is provably behaviour-
// preserving — an extracted helper with no test is just a relocated risk.
describe("describeCron", () => {
  it("renders an em dash for a missing expression", () => {
    expect(describeCron(null)).toBe("—");
  });

  it("names the every-minute special case", () => {
    expect(describeCron("* * * * *")).toBe("every minute");
  });

  it("renders a fixed daily time zero-padded", () => {
    expect(describeCron("0 9 * * *")).toBe("daily at 09:00");
    expect(describeCron("5 14 * * *")).toBe("daily at 14:05");
  });

  it("renders step minutes", () => {
    expect(describeCron("*/15 * * * *")).toBe("every 15 minutes");
  });

  it("renders step hours only when the minute field is a wildcard", () => {
    expect(describeCron("* */6 * * *")).toBe("every 6 hours");
  });

  // Pinned as-is, NOT as it should be. The daily-at branch tests `min !== "*" &&
  // hr !== "*"` and runs before the step-hours branch, so `0 */6 * * *` — a
  // perfectly ordinary "every 6 hours on the hour" cron — renders as the nonsense
  // string below. That predates this file: `describeCron` was moved here verbatim
  // out of OverviewScheduled.tsx, so the agent overview has always rendered it this
  // way. Asserting the real output keeps the extraction provably behaviour-
  // preserving; fixing the branch order would change what a live production screen
  // shows, which is not this change's business. Recorded in the gap ledger.
  it("mis-renders an on-the-hour step-hours cron (pre-existing, see comment)", () => {
    expect(describeCron("0 */6 * * *")).toBe("daily at */6:00");
  });

  it("falls back to the raw expression when it cannot summarise", () => {
    // A day-of-week cron is the shape the fleet actually uses most, and it has no
    // hint — worth pinning so nobody 'fixes' it into a wrong sentence.
    expect(describeCron("0 9 * * 1")).toBe("0 9 * * 1");
  });

  it("falls back when the field count is not five", () => {
    expect(describeCron("0 9 * *")).toBe("0 9 * *");
    expect(describeCron("0 9 * * 1 2")).toBe("0 9 * * 1 2");
  });
});

describe("cronHint", () => {
  it("returns the summary when there is one", () => {
    expect(cronHint("0 9 * * *")).toBe("daily at 09:00");
    expect(cronHint("*/15 * * * *")).toBe("every 15 minutes");
  });

  it("returns null when the summary would just echo the expression", () => {
    // Without this, a row that renders the raw cron AND a hint underneath shows
    // "0 9 * * 1" twice, which reads as a rendering bug and teaches the reader to
    // ignore the second line.
    expect(cronHint("0 9 * * 1")).toBeNull();
    expect(cronHint("0 9 * *")).toBeNull();
  });

  it("returns null for a missing expression rather than an em dash", () => {
    // describeCron renders "—" because its caller puts it in a value slot. A hint
    // slot wants nothing at all.
    expect(cronHint(null)).toBeNull();
  });
});
