# Skills became private by default with no way to ever publish one

**Found:** 2026-08-08, by inspection while scoping Decision 47 step E.
**Fixed:** NOT FIXED — ledgered as **G-E2**. This doc records the defect and why step E did
not absorb it.

## Symptom

Any skill created after migration `0080` is visible **only to its creator**, permanently.
There is no endpoint, no UI control, and no cascade that will ever set
`skills.publish_status = 'published'`.

## Root cause

Decision 47 says *"Skills get the same default change, or the inconsistency simply
relocates."* Step B did exactly that:

```
models.py:1370   # Private by default (Decision 47, migration 0080). See Tool.publish_status.
models.py:1371   publish_status: Mapped[str] = mapped_column(... default="private" ...)
```

and `catalog_visibility.py` serves both `list_tools` and `list_skills` from one producer, so
the filter bites for skills exactly as it does for tools.

What did **not** transfer is the forward path. Tools have one — Decision 47 option C makes
them ride along with an agent, via `publish_cascade.plan_tool_cascade`, which selects rows
from `tools` joined on `agent_tools`. Skills are not in that join and are not in any other.
The consumer exists with no producer:

```
routers/admin.py:377   elif pr.asset_type == "skill":
                           source_skill.publish_status = "published"
```

That branch is reachable only from a `PublishRequest` with `asset_type='skill'`, and
`grep -rn 'asset_type.*skill' services/registry-api` finds **no writer** — only this reader,
the CHECK constraints that permit the value, and a `team_assets` LEFT JOIN. So the approve
half was built and the submit half never was.

This is the same shape as `opa_decisions` (a complete table and router with zero writers)
and `adversarial_eval_passed` (a gate with no producer). A half-built pipeline reads as
finished from either end.

## Why step E did not fix it

Step E is the **reverse** direction: `POST /tools/{id}/unpublish`. Giving skills an
unpublish would be orphan code by construction — it could only ever return 409, because no
skill can reach `published` in the first place. Building the reverse before the forward
would add a second half-built pipeline to sit beside the existing one.

The fix is the missing forward path, and it is a design question, not a mechanical one:
skills are not bound to agents through a join table the way tools are, so "ride along with
an agent" has no obvious analogue. Choosing one is Decision-47-sized work, and inventing it
inside an unrelated step is how scope creep gets shipped as a side effect.

## Blast radius today

Bounded, and worth stating rather than assuming:

* Skills created **before** `0080` kept `published` — the migration did not backfill
  (Decision 47: *"No backfill. Do not clean this up later."*). The existing library is
  intact.
* Only skills created **after** `0080` are affected, and only their discoverability.
* `create_skill` sets `created_by` as of `0.2.271`, so the creator arm of
  `catalog_visibility_clause` does match — the author can still see and use their own skill.
  A skill created before that fix has a NULL creator and matches **neither** arm, making it
  invisible to everyone including its author; that specific hole was closed in `0.2.271`.

## Lessons

1. **"Give X the same default as Y" is not one change.** A default is half a lifecycle. The
   sentence in Decision 47 named the flip and assumed the path; the path was the work.
2. **A branch with no producer is not evidence a feature exists.** `admin.py:377` reads
   exactly like skill publishing is supported. Grep for the *writer* before believing a
   reader.
3. **Refusing to expand scope is only honest if the gap is written down.** This doc and
   G-E2 are the deliverable for the part step E did not do.
