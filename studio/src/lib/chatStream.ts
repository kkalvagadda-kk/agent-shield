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
