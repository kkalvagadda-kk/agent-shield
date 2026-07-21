/**
 * The bundle's identity — ONE definition, read by everything that reports it.
 *
 * MUST equal `STUDIO_TAG` in `scripts/deploy-cpe2e.sh` and `studio.image.tag` in
 * `charts/agentshield/values.yaml`. Bump all three together.
 *
 * WHY THIS FILE EXISTS: `window.__STUDIO_BUILD` was assigned in `main.tsx` and read by
 * NOTHING — `grep -rn "__STUDIO_BUILD" studio/src studio/e2e scripts charts` returned
 * exactly one line, its own assignment. It sat at "0.1.76" while STUDIO_TAG reached
 * "0.1.143": 67 tags of a marker that silently lied, because a value nothing reads
 * cannot fail loudly. It is now defined once and read twice (the window marker for
 * console/debug use, and a visible element in the Sidebar), so a stale bundle is
 * observable by a human and assertable by a test against the SERVED bytes.
 *
 * A tag is a claim about CONTENT (docs/bugs/e3-never-ran-tag-not-bumped.md): both tag
 * files once agreed on a stale tag and the cluster faithfully served old code while
 * every check stayed green. Only an assertion against what was actually served catches
 * that class — and that needs a reader.
 */
export const STUDIO_BUILD = "0.1.159";
