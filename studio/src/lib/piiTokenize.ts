/**
 * Display-only PII tokenizer.
 *
 * Eval v2 E-2 renders the side effects a record-mode eval INTERCEPTED — "the email
 * that would have been sent" — which means the reviewer's screen is the first place
 * a real recipient address / card number / SSN would surface. The E-2 gap ledger
 * inherits OQ-3's policy: recorded args are asserted **by value** server-side
 * (`judge.score_side_effects` dict-subset over the raw recorded args) and
 * **tokenized for display** here. Raw PII is never rendered.
 *
 * IMPORTANT: this is a DISPLAY transform, exactly like `shapeLabel` in ./utils —
 * never feed a tokenized value back into an assertion, a request body, or storage.
 * The stored/asserted value is always the raw one.
 *
 * Best-effort by design: it tokenizes the common high-signal shapes. It is a
 * courtesy screen for a reviewer looking at their own team's data, NOT a security
 * boundary (the args already crossed governance to reach the recording).
 */

// Order matters: the most specific shapes first, so a card/SSN is not swallowed by
// the looser phone pattern.
const PII_PATTERNS: ReadonlyArray<readonly [RegExp, string]> = [
  // foo.bar+tag@acme.co.uk
  [/[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g, "‹email›"],
  // 123-45-6789
  [/\b\d{3}-\d{2}-\d{4}\b/g, "‹ssn›"],
  // 4111 1111 1111 1111 / 4111-1111-1111-1111 / 4111111111111111
  [/\b(?:\d[ -]?){13,19}\b/g, "‹card›"],
  // +1 (555) 123-4567 / 555-123-4567
  [/\+?\d[\d\s().-]{7,}\d/g, "‹phone›"],
];

/** Replace PII-shaped substrings in one string with a stable token. */
export function tokenizePii(value: string): string {
  let out = value;
  for (const [pattern, token] of PII_PATTERNS) {
    // Fresh lastIndex per call — the /g regexes are module-level constants.
    pattern.lastIndex = 0;
    out = out.replace(pattern, token);
  }
  return out;
}

/**
 * Recursively tokenize every string in a recorded-call args object.
 * Non-string leaves (numbers, booleans, null) are structural, not PII carriers,
 * and pass through so the reviewer can still read the call's shape.
 */
export function tokenizePiiDeep(value: unknown): unknown {
  if (typeof value === "string") return tokenizePii(value);
  if (Array.isArray(value)) return value.map(tokenizePiiDeep);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, tokenizePiiDeep(v)]),
    );
  }
  return value;
}

/** Render recorded-call args as a compact, PII-tokenized JSON string for display. */
export function tokenizeArgsForDisplay(args: unknown): string {
  if (args == null) return "—";
  return JSON.stringify(tokenizePiiDeep(args));
}
