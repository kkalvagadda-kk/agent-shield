import type { RegistryTool } from "../api/registryApi";

/**
 * Who may take a tool back out of the org-wide catalog — the UI AFFORDANCE only.
 *
 * THE SERVER IS THE AUTHORITY, AND THIS IS NOT IT.
 * ------------------------------------------------
 * `POST /api/v1/tools/{id}/unpublish` calls `publish_cascade.may_unpublish_tool`, which
 * decides the same three arms server-side and 403s otherwise. This function exists only so
 * the page does not render an action that will certainly fail. A disagreement between the
 * two shows up as a button that 403s (or a missing button), never as an unauthorized write.
 *
 * WHY A SECOND COPY OF THE RULE AT ALL — the tradeoff, stated rather than hidden
 * ------------------------------------------------------------------------------
 * The obvious alternative was a server-computed `can_unpublish` flag on `ToolResponse`, and
 * it was rejected: `ToolResponse` is returned by routes that have no authenticated caller
 * (`GET /tools/{id}` takes no auth dependency at all), so those would have to send `false` —
 * a value meaning "not computed" that reads identically to "denied". A flag that lies on
 * three routes to be honest on one is worse than a client predicate that is openly a
 * predicate. If a future change gives every tool route a caller, delete this and move the
 * answer to the server, where it belongs.
 *
 * Keep the arms in the same order and with the same meaning as
 * `services/registry-api/publish_cascade.py::may_unpublish_tool`.
 */
export interface ToolActor {
  /** The JWT `sub`. Matched against `tool.created_by`. */
  sub: string | null;
  /** The caller's team from `user_team_assignments`, matched against `tool.owner_team`. */
  team: string | null;
  /** Normalized global role. Only `platform-admin` gets the third arm. */
  role: string | null;
}

export function canUnpublishTool(tool: RegistryTool, actor: ToolActor): boolean {
  // A private tool has nothing to take back. Checked first so the action never appears
  // on a row where the server would answer 409 rather than 403 — two different errors,
  // and neither belongs in front of a user who did nothing wrong.
  if (tool.publish_status !== "published") return false;

  // creator — "you published it by riding along with your agent; you can take it back"
  if (tool.created_by && actor.sub && tool.created_by === actor.sub) return true;

  // owning team — ownership is team-level (Decision 46), so a tool does not become
  // unmaintainable when the person who created it leaves.
  if (tool.owner_team && actor.team && tool.owner_team === actor.team) return true;

  // platform-admin — the actor who APPROVED the publish. Without this arm the only person
  // who can put a tool into the catalog cannot take it out, which is the one-way ratchet
  // Decision 47 #4 exists to break.
  return actor.role === "platform-admin";
}
