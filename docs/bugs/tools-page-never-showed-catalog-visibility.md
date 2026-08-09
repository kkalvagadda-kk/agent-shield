# The Tools page never showed catalog visibility — a permission-bearing field with no reader

**Found:** 2026-08-08, while scoping Decision 47 step E.
**Fixed:** 2026-08-08, studio `0.1.188` (same change as step E).

## Symptom

`grep -n "publish_status" studio/src/pages/ToolsPage.tsx` returned nothing. Every row on the
Tools screen rendered identically whether the tool was visible org-wide or was a private
draft only its creator could see. There was no badge, no column, and no filter.

The state was not missing — it was on the wire the whole time. `ToolResponse.publish_status`
is declared with **no default** (`schemas.py:722`), precisely so a missing value would
surface rather than be papered over. The server sent it on every row. The client threw it
away, because the TypeScript `RegistryTool` interface simply did not declare the field.

## Root cause

Decision 47 step B (`0.2.268`, migration `0080`) flipped `Tool.publish_status` from
`'published'` to `'private'` by default and made `catalog_visibility_clause` load-bearing.
Before that change the field was **inert**: essentially every row was `published`, so a
screen that ignored it was not lying about anything — there was only one population to show.

Step B changed the field from decorative to load-bearing and **did not add a reader**. That
is the whole defect. The class is one this repo already has entries for:

* `window.__STUDIO_BUILD` — assigned, read by nothing, drifted 67 tags
  (`studio/src/lib/build.ts` documents it).
* `opa_decisions` — a full table and router with zero writers.
* `Approval.opa_decision_id` — a FK never populated.

Same shape, opposite direction: here the producer was correct and the *consumer* was
missing. A value nothing reads cannot fail loudly, so nothing failed. The bash suites could
not catch it either — they `kubectl exec` into the pod and assert the API, and the API was
right.

The interface omission then propagated: because `RegistryTool` did not declare
`publish_status`, nothing in the frontend *could* branch on it without a type error being
the first hint, and no type error ever appeared, because nobody tried.

## Fix

`publish_status` and `created_by` are now **required** fields on `RegistryTool` (not
optional — the server declares them with no default for the same reason), and the Tools page
renders a `Visibility` column distinct from the existing operational `Status` column. Two
columns because they are two questions: `publish_status` is who can SEE it,
`status` is active/deprecated/inactive.

Making them required was the class fix, not the badge. It turned the omission into a
compile error at every fixture that had drifted from the type —
`CredentialsPage.test.tsx:47` failed `tsc` immediately and had to state what it meant.

## Why this mattered more than a missing badge

Step E gives owners a way to take a tool back out of the catalog. A reverse control is
useless if a person cannot see which state a tool is in — they would be reversing something
they were never shown, which is the same defect step D fixed on the reviewer's side
(`docs/decisions.md` Decision 47, gap G-R3-11). The two are the same argument applied to the
two ends of one lifecycle.

## Tests

* `studio/src/pages/ToolsPage.test.tsx` — "distinguishes Visibility from Status — two
  columns, two questions".
* `studio/e2e/tool-unpublish.spec.ts` — asserts the badge reads `Published` before the
  action and `Private` after a full page reload. The reload is the assertion; the
  optimistic cache is not.

## Lessons

1. **A default flip is a UI change.** Migration `0080` turned an inert field into a
   meaningful one. The moment a column starts carrying two populations, every screen that
   lists those rows owes the user the distinction.
2. **An optional field in a client type is where a required server field goes to die.** The
   server's "no default, a missing one is corruption" discipline (`schemas.py:717-722`) only
   survives the wire if the client declares it required too.
3. **Grep the consumer, not just the producer.** Step B's checklist verified the migration,
   the resolver and the SDK. Nothing asked "which screen shows this now that it means
   something".
