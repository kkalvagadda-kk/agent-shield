import { describe, expect, it } from "vitest";
import { armDetail, armLabel, armTone, isArmed, needsAttention } from "./triggerArm";

describe("isArmed / armTone / armLabel", () => {
  it("reads arm state off `enabled` — the only column that exists", () => {
    expect(isArmed({ enabled: true })).toBe(true);
    expect(armTone({ enabled: true })).toBe("armed");
    expect(armLabel({ enabled: true })).toBe("Armed");
  });

  it("calls a disabled trigger disarmed", () => {
    expect(isArmed({ enabled: false })).toBe(false);
    expect(armTone({ enabled: false })).toBe("disarmed");
    expect(armLabel({ enabled: false })).toBe("Disarmed");
  });

  it("does not consult any other field", () => {
    // Regression: this derived from `armed_at`, which the server synthesised from
    // `created_at` — non-null on every row, so deleted agents the lifecycle gate had
    // just disarmed rendered an "Armed" pill beside "this schedule is disabled".
    // If a second field ever creeps back in here, that bug is back with it.
    expect(
      isArmed({ enabled: false, ...({ armed_at: "2026-07-14T09:00:00Z" } as object) }),
    ).toBe(false);
  });
});

describe("armDetail", () => {
  it("names who authorized an armed schedule", () => {
    expect(armDetail({ enabled: true, armed_by: "kalyan" })).toBe("by kalyan");
  });

  it("gives an armed schedule with no recorded authorizer no story at all", () => {
    expect(armDetail({ enabled: true, armed_by: null })).toBeNull();
  });

  it("surfaces the disarm reason, dated, when disarmed", () => {
    const d = armDetail({
      enabled: false,
      disarm_reason: "workflow archived",
      disarmed_at: "2026-07-14T09:00:00Z",
    });
    expect(d).toContain("workflow archived");
    expect(d).toContain("·");
  });

  it("shows an undated reason verbatim rather than inventing a date", () => {
    expect(armDetail({ enabled: false, disarm_reason: "workflow archived" })).toBe(
      "workflow archived",
    );
  });

  it("invents nothing for a disarmed trigger with no reason", () => {
    // A fabricated story here would read as a recorded fact on the page.
    expect(armDetail({ enabled: false, armed_by: null, disarm_reason: null })).toBeNull();
  });
});

describe("needsAttention", () => {
  it("flags an armed schedule on a dead artifact — the zombie", () => {
    expect(needsAttention({ enabled: true, will_fire: false })).toBe(true);
  });

  it("flags a schedule the author left switched on that cannot dispatch", () => {
    expect(needsAttention({ enabled: true, will_fire: false, disarm_reason: null })).toBe(true);
  });

  it("does not flag a healthy schedule", () => {
    expect(needsAttention({ enabled: true, will_fire: true })).toBe(false);
  });

  it("does not flag a disarmed schedule", () => {
    // Not firing on purpose. Counting it would make the badge cry wolf, which
    // trains operators to stop reading it.
    expect(needsAttention({ enabled: false, will_fire: false })).toBe(false);
  });

  it("still refuses to flag an enabled row carrying a stale disarm reason", () => {
    // Both PATCH handlers now clear the reason on re-enable via one shared helper
    // (trigger_lifecycle.apply_trigger_update), so this pairing should be
    // unreachable — the workflow handler used to skip that clearing, which is
    // precisely why the guard stays rather than being deleted as dead.
    expect(
      needsAttention({ enabled: true, will_fire: false, disarm_reason: "workflow archived" }),
    ).toBe(false);
  });
});
