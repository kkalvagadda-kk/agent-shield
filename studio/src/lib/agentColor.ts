// ---------------------------------------------------------------------------
// agentColor.ts — deterministic per-agent color assignment.
//
// A multi-agent workflow reads as a real multi-speaker conversation only if each
// agent keeps the SAME color everywhere it appears — across bubbles, across
// reloads, across sessions. So the color is a pure function of the agent name:
// an FNV-1a hash of the name, mod a FIXED 8-entry Tailwind palette.
//
// The palette entries are STATIC class strings (e.g. "bg-blue-50"), never
// interpolated (`bg-${x}`) — Tailwind's JIT only emits classes it can see as
// literals in source, so a dynamic class name would silently render unstyled.
// ---------------------------------------------------------------------------

export interface AgentColor {
  bg: string;
  text: string;
  border: string;
  dot: string;
}

// Fixed 8-entry palette. Each entry's classes must appear as literals here so
// Tailwind emits them. Ordered for visual spread (distinct hues adjacent).
const PALETTE: AgentColor[] = [
  { bg: "bg-blue-50", text: "text-blue-700", border: "border-blue-200", dot: "bg-blue-500" },
  {
    bg: "bg-emerald-50",
    text: "text-emerald-700",
    border: "border-emerald-200",
    dot: "bg-emerald-500",
  },
  {
    bg: "bg-purple-50",
    text: "text-purple-700",
    border: "border-purple-200",
    dot: "bg-purple-500",
  },
  { bg: "bg-amber-50", text: "text-amber-700", border: "border-amber-200", dot: "bg-amber-500" },
  { bg: "bg-rose-50", text: "text-rose-700", border: "border-rose-200", dot: "bg-rose-500" },
  { bg: "bg-cyan-50", text: "text-cyan-700", border: "border-cyan-200", dot: "bg-cyan-500" },
  {
    bg: "bg-indigo-50",
    text: "text-indigo-700",
    border: "border-indigo-200",
    dot: "bg-indigo-500",
  },
  { bg: "bg-teal-50", text: "text-teal-700", border: "border-teal-200", dot: "bg-teal-500" },
];

// Neutral entry for empty/undefined names (unattributed bubbles).
const NEUTRAL: AgentColor = {
  bg: "bg-gray-50",
  text: "text-gray-700",
  border: "border-gray-200",
  dot: "bg-gray-400",
};

/** FNV-1a 32-bit hash of a string. Deterministic across runtimes. */
function fnv1a(input: string): number {
  let hash = 0x811c9dc5; // FNV offset basis
  for (let i = 0; i < input.length; i++) {
    hash ^= input.charCodeAt(i);
    // 32-bit FNV prime multiply via shifts, kept in an unsigned range.
    hash = Math.imul(hash, 0x01000193);
  }
  return hash >>> 0; // force unsigned 32-bit
}

/**
 * Deterministic per-agent color: same name → same palette entry, across reloads
 * and sessions. Empty/undefined/null names return a neutral entry.
 */
export function agentColor(name: string | undefined | null): AgentColor {
  if (!name) return NEUTRAL;
  const idx = fnv1a(name) % PALETTE.length;
  return PALETTE[idx];
}

/** Exposed for tests: how many distinct entries the palette holds. */
export const AGENT_COLOR_PALETTE_SIZE = PALETTE.length;
