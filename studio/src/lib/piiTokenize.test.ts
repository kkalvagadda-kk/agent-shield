import { describe, it, expect } from "vitest";
import { tokenizePii, tokenizePiiDeep, tokenizeArgsForDisplay } from "./piiTokenize";

// Eval v2 E-2 — the recorded side effects a record-mode eval intercepted are rendered
// to a reviewer, so the args must never carry raw PII to the screen (E-2 gap ledger,
// inherited OQ-3 policy: assert by value server-side, tokenize for display).
describe("tokenizePii", () => {
  it("tokenizes an email address", () => {
    expect(tokenizePii("send to compliance@acme.com now")).toBe("send to ‹email› now");
  });

  it("tokenizes an SSN", () => {
    expect(tokenizePii("ssn 123-45-6789")).toBe("ssn ‹ssn›");
  });

  it("tokenizes a card number, spaced or joined", () => {
    expect(tokenizePii("4111 1111 1111 1111")).toBe("‹card›");
    expect(tokenizePii("4111111111111111")).toBe("‹card›");
  });

  it("tokenizes a phone number", () => {
    expect(tokenizePii("call 555-123-4567")).toBe("call ‹phone›");
  });

  it("leaves non-PII text untouched", () => {
    expect(tokenizePii("Q3 breach report")).toBe("Q3 breach report");
    expect(tokenizePii("order 123")).toBe("order 123");
  });
});

describe("tokenizePiiDeep", () => {
  it("tokenizes strings nested in objects and arrays", () => {
    expect(
      tokenizePiiDeep({
        to: "compliance@acme.com",
        cc: ["a@b.co", "plain text"],
        meta: { subject: "Q3", note: "reach me at 555-123-4567" },
      }),
    ).toEqual({
      to: "‹email›",
      cc: ["‹email›", "plain text"],
      meta: { subject: "Q3", note: "reach me at ‹phone›" },
    });
  });

  it("passes structural non-string leaves through", () => {
    expect(tokenizePiiDeep({ count: 3, ok: true, none: null })).toEqual({
      count: 3,
      ok: true,
      none: null,
    });
  });
});

describe("tokenizeArgsForDisplay", () => {
  it("renders tokenized JSON", () => {
    expect(tokenizeArgsForDisplay({ to: "compliance@acme.com", subject: "Q3" })).toBe(
      '{"to":"‹email›","subject":"Q3"}',
    );
  });

  it("renders an em-dash for absent args", () => {
    expect(tokenizeArgsForDisplay(null)).toBe("—");
    expect(tokenizeArgsForDisplay(undefined)).toBe("—");
  });

  it("is stable across repeated calls (module-level /g regex lastIndex)", () => {
    const args = { to: "compliance@acme.com" };
    const first = tokenizeArgsForDisplay(args);
    expect(tokenizeArgsForDisplay(args)).toBe(first);
    expect(tokenizeArgsForDisplay(args)).toBe(first);
  });
});
