// Cron rendering helpers.
//
// `describeCron` was defined inside OverviewScheduled.tsx. The Schedules page needs
// the identical hint, and a second copy is how two surfaces start describing the
// same cron differently. Moved here verbatim — same move `lib/evalVerdict.ts` made
// in studio 0.1.167, for the same reason.

/** Lightweight human hint for the common cron shapes (no external dep). */
export function describeCron(expr: string | null): string {
  if (!expr) return "—";
  const parts = expr.trim().split(/\s+/);
  if (parts.length !== 5) return expr;
  const [min, hr, dom, mon, dow] = parts;
  if (expr === "* * * * *") return "every minute";
  if (min !== "*" && hr !== "*" && dom === "*" && mon === "*" && dow === "*")
    return `daily at ${hr.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (min.startsWith("*/")) return `every ${min.slice(2)} minutes`;
  if (hr.startsWith("*/")) return `every ${hr.slice(2)} hours`;
  return expr;
}

/**
 * The human hint, or null when there isn't one.
 *
 * `describeCron` echoes its input when it cannot summarise, which is the right
 * fallback for a caller that renders one string. It is the wrong thing for a caller
 * that renders the raw cron AND a hint underneath it: `0 9 * * 1` then reads
 * "0 9 * * 1" twice, which looks like a rendering bug and teaches the reader to
 * ignore the second line.
 */
export function cronHint(expr: string | null): string | null {
  if (!expr) return null;
  const described = describeCron(expr);
  return described === expr ? null : described;
}
