import { describe, expect, it, vi } from "vitest";
import { buildAuthValue } from "./AuthContext";

// getKeycloak() reaches for a real adapter; the role hierarchy under test does
// not depend on it, so a null instance is enough.
vi.mock("../lib/keycloak", () => ({
  getKeycloak: () => null,
}));

describe("buildAuthValue — isAtLeast role hierarchy", () => {
  it("orders the canonical roles consumer < contributor < platform-admin", () => {
    const consumer = buildAuthValue(null, "default", "consumer");
    expect(consumer.isAtLeast("consumer")).toBe(true);
    expect(consumer.isAtLeast("contributor")).toBe(false);
    expect(consumer.isAtLeast("platform-admin")).toBe(false);

    const contributor = buildAuthValue(null, "default", "contributor");
    expect(contributor.isAtLeast("consumer")).toBe(true);
    expect(contributor.isAtLeast("contributor")).toBe(true);
    expect(contributor.isAtLeast("platform-admin")).toBe(false);

    const admin = buildAuthValue(null, "default", "platform-admin");
    expect(admin.isAtLeast("consumer")).toBe(true);
    expect(admin.isAtLeast("contributor")).toBe(true);
    expect(admin.isAtLeast("platform-admin")).toBe(true);
  });

  // Regression guard: `viewer` was renamed to `consumer`. A row or JWT still
  // carrying a legacy spelling must keep its level — if it fell through to the
  // `?? 0` default, a legacy `admin` would be silently demoted to read-only and
  // lose the whole admin section.
  it.each([
    ["viewer", "consumer", false],
    ["operator", "contributor", false],
    ["admin", "platform-admin", true],
  ])("maps legacy role %s to the level of %s", (legacy, _canonical, isAdmin) => {
    const v = buildAuthValue(null, "default", legacy);
    expect(v.isAtLeast("consumer")).toBe(true);
    expect(v.isAtLeast("platform-admin")).toBe(isAdmin);
  });

  it("keeps legacy and canonical spellings at identical levels", () => {
    const pairs: [string, string][] = [
      ["viewer", "consumer"],
      ["operator", "contributor"],
      ["admin", "platform-admin"],
    ];
    for (const [legacy, canonical] of pairs) {
      const l = buildAuthValue(null, "default", legacy);
      const c = buildAuthValue(null, "default", canonical);
      for (const min of ["consumer", "contributor", "platform-admin"] as const) {
        expect(l.isAtLeast(min)).toBe(c.isAtLeast(min));
      }
    }
  });

  it("defaults a null role to the consumer floor, not to admin", () => {
    const v = buildAuthValue(null, "default", null);
    expect(v.role).toBeNull();
    expect(v.isAtLeast("consumer")).toBe(true);
    expect(v.isAtLeast("contributor")).toBe(false);
    expect(v.isAtLeast("platform-admin")).toBe(false);
  });

  it("treats an unrecognized role as the lowest level", () => {
    const v = buildAuthValue(null, "default", "not-a-real-role");
    expect(v.isAtLeast("consumer")).toBe(true);
    expect(v.isAtLeast("contributor")).toBe(false);
    expect(v.isAtLeast("platform-admin")).toBe(false);
  });
});
