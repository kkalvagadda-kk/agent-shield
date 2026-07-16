// ---------------------------------------------------------------------------
// chatStream.ts — pure SSE stream reducers for attributed chat bubbles.
//
// Three chat surfaces (AgentChatPage, ChatPane, CatalogChatPage) each consume a
// stream of token deltas that may carry an `author` (the speaking agent). These
// helpers are the SHARED, contract-agnostic logic for turning that stream into
// an array of attributed bubbles. They are pure (no React) so they can back a
// `setState(prev => ...)` reducer in any surface and be unit-tested directly.
//
// The `author` is supplied EXPLICITLY by each adapter (proxy frame, playground
// prop, run-tree row) — the reducer never sniffs the surface. Single-agent is
// the degenerate one-speaker case (`author` undefined), not a code fork.
// ---------------------------------------------------------------------------

/** Minimal shape the reducers operate over. Surfaces extend this with their own
 *  fields (id, chips, safety, etc.) via the generic `M`. */
export interface Attributed {
  role: string;
  content: string;
  author?: string;
}

/** True when a bubble is an assistant bubble authored by (or open to) `author`.
 *  An incoming undefined author (single-speaker stream) matches any assistant
 *  bubble — there is only one speaker, so we never fork. */
function isOpenAssistantFor<M extends Attributed>(bubble: M, author: string | undefined): boolean {
  if (bubble.role !== "assistant") return false;
  if (author === undefined) return true;
  return bubble.author === author;
}

/**
 * Append `content` to the last assistant bubble when it is open for `author`
 * (matching author, or `author` undefined = single-speaker); otherwise open a
 * new assistant bubble via `make(author)` seeded with `content`. Returns a new
 * array (immutable update) suitable for a React setState reducer.
 */
export function routeToken<M extends Attributed>(
  messages: M[],
  author: string | undefined,
  content: string,
  make: (author?: string) => M
): M[] {
  const last = messages[messages.length - 1];
  if (last && isOpenAssistantFor(last, author)) {
    const updated: M = { ...last, content: last.content + content };
    return [...messages.slice(0, -1), updated];
  }
  const fresh: M = { ...make(author), content };
  return [...messages, fresh];
}

/**
 * Open a fresh empty assistant bubble for `author` (handles an `agent_start`
 * frame). If the last bubble is already an empty, open assistant bubble for the
 * same author, this is a no-op (avoids stacking blank bubbles when agent_start
 * and the first token both arrive). Returns a new array.
 */
export function openAuthorBubble<M extends Attributed>(
  messages: M[],
  author: string | undefined,
  make: (author?: string) => M
): M[] {
  const last = messages[messages.length - 1];
  if (last && isOpenAssistantFor(last, author) && last.content === "") {
    return messages;
  }
  return [...messages, make(author)];
}

/** A single tool call the member pod reported (POC-2b tool-call chip). */
export interface ToolCall {
  tool_name: string;
  status: string; // "ok" | "error"
}

/** Attributed bubble enriched with the POC-2b rich slots (tool chips + the
 *  member's one-line rationale). Surfaces still extend this with their own
 *  fields via the generic `M`. Both fields are optional so a single-agent
 *  bubble that never sets them is byte-identical to a plain `Attributed`. */
export interface AttributedRich extends Attributed {
  toolCalls?: ToolCall[];
  rationale?: string | null;
}

/**
 * Attach a `tool_call` frame to the open assistant bubble for `author`
 * (appending to its `toolCalls` list); if the last bubble is not an open
 * assistant bubble for that author, open a new one seeded with the tool call.
 * Pure/immutable — returns a new array. Reuses `isOpenAssistantFor` so the
 * single-speaker (`author` undefined) case never forks.
 */
export function attachToolCall<M extends AttributedRich>(
  messages: M[],
  author: string | undefined,
  toolCall: ToolCall,
  make: (author?: string) => M
): M[] {
  const last = messages[messages.length - 1];
  if (last && isOpenAssistantFor(last, author)) {
    const updated: M = { ...last, toolCalls: [...(last.toolCalls ?? []), toolCall] };
    return [...messages.slice(0, -1), updated];
  }
  const fresh: M = { ...make(author), toolCalls: [toolCall] };
  return [...messages, fresh];
}

/**
 * Set the `rationale` on the open assistant bubble for `author`; if the last
 * bubble is not an open assistant bubble for that author, open a new one seeded
 * with the rationale. Pure/immutable — returns a new array.
 */
export function attachRationale<M extends AttributedRich>(
  messages: M[],
  author: string | undefined,
  rationale: string,
  make: (author?: string) => M
): M[] {
  const last = messages[messages.length - 1];
  if (last && isOpenAssistantFor(last, author)) {
    const updated: M = { ...last, rationale };
    return [...messages.slice(0, -1), updated];
  }
  const fresh: M = { ...make(author), rationale };
  return [...messages, fresh];
}
