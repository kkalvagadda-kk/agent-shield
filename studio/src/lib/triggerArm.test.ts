import { describe, expect, it } from "vitest";
import { armDetail, armLabel, armTone, isArmed, needsAttention } from "./triggerArm";

describe("isArmed / armTone / armLabel", () => {
  it("treats a present armed_at as armed", () => {
    expect(isArmed({ armed_at: "2026-07-14T09:00:00Z" })).toBe(true);
    expect(armTone({ armed_at: "2026-07-14T09:00:00Z" })).toBe("armed");
    expect(armLabel({ armed_at: "2026-07-14T09:00:00Z" })).toBe("Armed");
  });

  it("treats null and undefined armed_at as disarmed", () => {
    expect(isArmed({ armed_at: null })).toBe(false);
    expect(isArmed({})).toBe(false);
    expect(armTone({})).toBe("disarmed");
    expect(armLabel({})).toBe("Disarmed");
  });

  it("does not read `enabled` — arm state and the pause switch are orthogonal", () => {
    // A trigger the author paused keeps its arming. If this ever starts depending
    // on `enabled`, the two-jobs-one-boolean bug has been reintroduced.
    const paused = { armed_at: "2026-07-14T09:00:00Z" };
    expect(isArmed({ ...paused, ...({ enabled: false } as object) })).toBe(true);
  });
});

describe("armDetail", () => {
  it("names who armed it and when", () => {
    const d = armDetail({ armed_at: "2026-07-14T09:00:00Z", armed_by: "kalyan" });
    expect(d).toContain("by kalyan");
    expect(d).toContain("on ");
  });

  it("falls back to the armer alone when there is no timestamp", () => {
    expect(armDetail({ armed_at: null, armed_by: "kalyan", disarm_reason: null })).toBeNull();
  });

  it("surfaces the disarm reason verbatim when disarmed", () => {
    expect(armDetail({ armed_at: null, disarm_reason: "workflow archived" })).toBe(
      "workflow archived",
    );
  });

  it("invents nothing for a never-armed trigger with no reason", () => {
    // A fabricated story here would read as a recorded fact on the page.
    expect(armDetail({ armed_at: null, armed_by: null, disarm_reason: null })).toBeNull();
  });
});

describe("needsAttention", () => {
  it("flags an armed schedule on a dead artifact — the zombie", () => {
    expect(needsAttention({ enabled: true, will_fire: false })).toBe(true);
  });

  it("flags a never-armed schedule the author left switched on", () => {
    expect(needsAttention({ enabled: true, will_fire: false, disarm_reason: null })).toBe(true);
  });

  it("does not flag a healthy schedule", () => {
    expect(needsAttention({ enabled: true, will_fire: true })).toBe(false);
  });

  it("does not flag a deliberately paused schedule", () => {
    // Not firing on purpose. Counting it would make the badge cry wolf, which
    // trains operators to stop reading it.
    expect(needsAttention({ enabled: false, will_fire: false })).toBe(false);
  });

  it("does not flag a schedule an operator disarmed", () => {
    // Regression: the badge stayed at 11 after disarming a row, because a disarmed
    // trigger is still `enabled` and still not firing. Alarming about a state the
    // operator just chose is the badge complaining about its own success.
    expect(
      needsAttention({ enabled: true, will_fire: false, disarm_reason: "disarmed by operator" }),
    ).toBe(false);
  });

  it("does not flag a schedule lifecycle disarmed on archive", () => {
    // This is the Phase-B fix working. It must not read as a problem.
    expect(
      needsAttention({ enabled: true, will_fire: false, disarm_reason: "workflow archived" }),
    ).toBe(false);
  });
});
