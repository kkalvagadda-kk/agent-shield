# Claude-in-Chrome Lifecycle Journey — browser-plugin acceptance test

**What this is.** A human-watchable acceptance test: Claude drives a **real, visible Chrome**
(via the Claude-in-Chrome extension) through the whole product lifecycle against the deployed
Studio. You watch each step happen and see its result. This is the test you review + expand
here FIRST, then tell me "run the Claude-in-Chrome journey" and I execute it live, capturing a
screenshot (and optional GIF) at each **Assert**.

It complements — does not replace — the headless Playwright `lifecycle-journey.spec.ts`. Same
10 legs; this one you can see.

---

## How it runs (when you say go)
1. **Target Studio URL** — the deployed gateway (e.g. `https://<elb>.elb.us-west-2.amazonaws.com`).
   I open a NEW tab (never reuse yours) and navigate there.
2. **Login** — if it redirects to Keycloak, I sign in as `platform-admin` (you provide the
   password via the credential flow — I never type it myself) OR you're already logged in.
3. **Fixtures** — I pre-seed the two slow-to-build fixtures via API before driving the UI:
   - a deterministic **high-risk HTTP tool** `cic-echo-tool` (points at the in-cluster `/echo`),
   - a **reactive eval dataset** `cic-journey-dataset`.
   (You approve this seed step; everything else is done through the browser.)
4. I drive the legs below **in order**, pausing on any failed Assert to show you the screenshot.

**Conventions in the steps below:** _Do_ = a browser action (navigate / click / type / press).
_Assert_ = something I read off the page and verify (text present, element visible, URL). The
plugin locates elements by **visible text / vision**, so targets are described by their label.

---

## Legs

### Leg 1 — Create an agent with a tool
- Do: navigate to `/agents/new`.
- Do: click **No-code**.
- Do: type `cic-journey-agent` into the agent-name field (placeholder "my-agent").
- Do: click **Add from catalog** to open the tools drawer.
- Do: in the drawer, find the tile **cic-echo-tool** and select it (click its checkbox/tile);
  click **Done**.
- Assert: the picker shows a **cic-echo-tool** chip (tool attached).
- Do: click **Create Agent**.
- Assert: the page lands on the **agents list** and `cic-journey-agent` appears in it.

### Leg 2 — Create a 2-agent workflow with an edge
- Precondition: a second agent `cic-journey-agent2` exists (seeded via API alongside the fixtures).
- Do: navigate to `/workflows/new`.
- Do: **Add Agent** → add `cic-journey-agent`, then **Add Agent** again → add `cic-journey-agent2`.
- Do: draw/confirm a sequential edge from agent-1 → agent-2 (or confirm the seeded edge renders).
- Do: **Save** → name it `cic-journey-wf` → confirm.
- Assert: the canvas shows **2 nodes** and **1 edge**.
- Do: **reload** the builder page.
- Assert: still **2 nodes + 1 edge** (persisted — save→reload→survived).

### Leg 3 — Deploy the agent to sandbox
- Do: navigate to `/agents/cic-journey-agent`.
- Do: click **Deploy** → in the modal **Deploy to sandbox** click **Deploy**.
- Assert: a toast/row confirms a sandbox deployment was created (status "deploying"/"running").
- Do: wait (poll the deployment status) up to ~2 min for **running**.
- Assert (tolerant): deployment reaches **running**, OR record "no warm pod" and continue.

### Leg 4 — Functional + per-response bubbles (the merged-bubble fix)
- Do: navigate to `/playground`; select `cic-journey-agent` in the left selector.
- Do: send a message that forces a tool call then an answer, e.g.
  `use the echo tool with q=hello, then tell me what it returned`.
- Assert: the reply renders as **separate bubbles per turn** (reasoning / tool-call / answer are
  NOT one merged blob) — the Issue-2 fix. Capture a screenshot of the bubbles.
- Tolerant: if no warm pod, record skip (bubble-split is also proven by unit + backend smoke).

### Leg 5 — Conversation saved even with memory OFF (the reported conversation bug) ⭐
This is the leg that must catch "no conversations saved when memory is off."
- Precondition: `cic-journey-agent` has memory **off** (no-code default) — confirm on its
  Settings tab that Memory is disabled.
- Do: seed a conversation via API on this memory-off agent (thread `cic-thr-1`):
  user "my name is Ada" / assistant "hello Ada".
  - Assert: the seed **succeeds** (HTTP 2xx). _If it 400s "memory not enabled", the bug is back._
- Do: navigate to `/agents/cic-journey-agent/chat?session=cic-thr-1`.
- Assert: the History transcript **rehydrates** — the message **"my name is Ada"** is visible.
- Do (live, tolerant): send "the sky is teal today", then "what color did I say the sky is?".
- Assert (tolerant, needs warm pod): the reply references **teal** (the agent actually recalled).

### Leg 6 — Evaluate the agent + view the trace
- Do: navigate to `/playground/datasets`; on `cic-journey-dataset` click **Run Eval** → pick the
  `cic-journey-agent` sandbox deployment → **Start Eval**.
- Do: on the eval-run page, wait for it to reach **completed/failed** (tolerant if no eval pod).
- Do: open a result row's **Trace**.
- Assert: the trace drawer renders **spans** (from Langfuse OR the durable run_steps fallback —
  the Issue-3 fix), and the trace-id is shown. If a Langfuse deep-link is present, it opens.

### Leg 7 — Publish (eval + adversarial gate)
- Do: in the Playground with `cic-journey-agent`'s version selected, click **Mark Version Passed**.
- Do: click **Publish Agent** WITHOUT marking adversarial.
- Assert: publish is **rejected** with the adversarial-gate message (the tool is high-risk) —
  proves the gate is live.
- Do: click **Mark Adversarial Passed**, then **Publish Agent** again.
- Assert: publish **succeeds** (a publish request is created / status → pending review).

### Leg 8 — Admin approves → marketplace
- Do: navigate to `/admin/publish-requests`.
- Do: on the `cic-journey-agent` row click **Promote**.
- Assert: a success toast; then navigate to `/catalog` and confirm `cic-journey-agent` is listed
  (grant the owner team first if the card isn't visible).

### Leg 9 — Deploy from the marketplace + consumer chat
- Do: open `/catalog/<artifact>`; click **Deploy Latest**.
- Assert: a production deployment is created (status pending → running, tolerant).
- Do: open the consumer **chat**; send a message.
- Assert (tolerant): the consumer surface responds; the conversation is saved (visible in the
  consumer History on reload).

### Leg 10 — Observability reflects the runs (before/after)
- Do: BEFORE any of the above runs, note **Total Runs** on `/observability/dashboard/sandbox`
  (I capture this number at the start).
- Do: navigate to `/observability/dashboard/sandbox` now.
- Assert: the dashboard renders (Total Runs, Status Distribution panels); **Total Runs ≥ the
  before number** (a run that fired increments it; sandbox vs production stays scoped).
- Do: open `/observability/traces`; Assert a trace row for the journey agent appears.
- Note: cost / spend-by-model / judge-score are Langfuse-derived — assert the panels **render**,
  not a specific number.

---

## Expanded legs (11–18)

These flesh out the earlier checklist plus the coverage I'd insist on for a real acceptance
run. Same **Do / Assert** format. Legs 11 and 12 are the two I'd prioritize — they cover the
exact reported bugs directly through the browser.

### Leg 11 — Conversation survives LEAVING and returning (the reported "lost on leaving" bug) ⭐
Directly reproduces "conversation is lost the moment I leave the screen."
- Do: open `/agents/cic-journey-agent/chat`; send "remember the code is 4917".
- Assert (tolerant): a reply renders.
- Do: navigate AWAY — click to `/agents` (or another nav item) — then come BACK to the agent's
  chat (via History / the deployment's Conversations tab).
- Assert: the earlier turn "remember the code is 4917" is **still there** (not a blank pane).
- Do: hard-**reload** the browser tab on the chat page.
- Assert: the transcript **rehydrates** from the backend — the turn is still visible.
  _(If it's blank after leaving/reloading, the Issue-1 / F-F regression is back.)_

### Leg 12 — SANDBOX HITL: inline self-approval (playground/sandbox context)
In sandbox/playground context a high-risk tool call parks as a **self-service** approval decided
**INLINE in the run panel** — it is deliberately NOT routed to the reviewer console. `cic-echo-tool`
is high-risk, so a call to it triggers this.
- Do: in the Playground with `cic-journey-agent` (sandbox), send a message that calls `cic-echo-tool`.
- Assert: an **inline approval panel** appears in the run panel (tool name, risk level, args) and
  streaming pauses; **no** entry shows up in the reviewer Approvals console.
- Do: click **Approve** (self-approve, in place).
- Assert: streaming **resumes** inline and the run completes (grounded answer, no error bubble,
  no orphaned approval).
- Do (negative): repeat, click **Deny**.
- Assert: the run ends without executing the tool; the panel states it was denied.

### Leg 12b — PRODUCTION HITL: reviewer-console approval (marketplace/consumer context)
In production the SAME tool call behaves differently — it parks to the **reviewer Approvals
console** (authority-scoped) and the consumer just **waits**; the caller cannot self-approve.
Requires the marketplace/production deployment (leg 9) and a reviewer identity holding
ApprovalAuthority for the artifact.
- Do: on the consumer chat (`/catalog/<artifact>/chat`), send a message that calls the high-risk tool.
- Assert: the consumer surface shows an **"awaiting approval"** banner (NOT an inline self-approve
  control) — the run is parked, streaming paused, waiting on a reviewer.
- Do: switch to the **reviewer**; open the **Approvals console** (`/approvals` inbox).
- Assert: the pending approval for this run is listed with **who** asked, tool, risk, and args.
- Do: the reviewer clicks **Approve** in the console.
- Assert: the consumer run **resumes** and completes; the approval leaves the inbox.
- Do (negative): confirm the consumer surface offers the caller **no** self-approve control — only
  the console reviewer can decide (the production/sandbox authority split).
- Note: sandbox (leg 12) and production (leg 12b) must BOTH be exercised — they are different code
  paths (`AGENTSHIELD_PLAYGROUND/SANDBOX` inline-self-approve vs the reviewer-console routing), and
  a change to one has historically not covered the other.

### Leg 13 — Trace panel populates + Langfuse deep-link (Issue-3 fix, in the UI)
- Do: after a completed run, open its **trace** (drawer or `/observability/traces` → row → Eye).
- Assert: **spans render** (a waterfall with node/tool/generation rows) and a **trace-id** shows.
- Do: if a **Trace** deep-link is present, click it.
- Assert: it opens Langfuse in a new tab and the **same trace** loads there (external proof).
- Assert (fallback): if Langfuse is down, the drawer STILL shows spans (durable run_steps
  fallback) — not an empty "no observations" panel.

### Leg 14 — Reasoning renders as its own block, separate from the answer (Issue-2 detail)
- Do: send a prompt that induces reasoning + a final answer.
- Assert: a distinct **Reasoning** block appears above/around the answer bubble — the model's
  thinking is NOT merged into the answer text. (Contingent on Bedrock returning reasoning; if
  none this run, assert at least the per-turn bubble split.)

### Leg 15 — Sandbox vs Production dashboards stay scoped
- Do: note **Total Runs** on `/observability/dashboard/sandbox`, then on `/dashboard/production`.
- Do: fire a SANDBOX run (a Playground chat), refresh both dashboards.
- Assert: the **sandbox** Total Runs increments; the **production** number is **unchanged**
  (a sandbox run never dilutes production metrics — the hard FK scoping).

### Leg 16 — Negative: an agent WITHOUT adversarial pass cannot go to production
- Do: with an agent that has a high-risk tool but has NOT been marked adversarial-passed, attempt
  to publish/deploy to production.
- Assert: it is **rejected** with the adversarial-gate message (422). Then mark adversarial passed
  and retry → **allowed**. (Proves the gate isn't a no-op and isn't over-blocking.)

### Leg 17 — Version management: new version → deploy the new one
- Do: on `cic-journey-agent`, edit something (e.g. system prompt) and save → creates a new version.
- Assert: the version list shows **2 versions**; the new one is selectable.
- Do: deploy the new version to sandbox.
- Assert: the running deployment pins the **new** version id.

### Leg 18 — Cost dashboard reflects spend (only once Langfuse ingests Bedrock cost)
- Do: open `/observability/costs`.
- Assert (env-dependent): with a real Bedrock run ingested, **Total Spend > 0** and Spend-by-model
  lists the Bedrock model. If cost hasn't ingested yet, assert the panels **render** (headings
  present) — do NOT hard-assert a number until ingestion is confirmed.

---

### Leg 19 — Workflow conversation saves + rehydrates (WorkflowChatPage)
Parallel to leg 5, but on a workflow — a different persistence path (the parent run stamps
`user_id` + `session_id` via `/runs/stream`, and members' transcript rows are attributed to it).
- Do: open `cic-journey-wf`'s chat (deploy it, or use the builder run panel); send/seed a
  conversation turn keyed to a session.
- Do: leave + return (or reload) the workflow chat with that session.
- Assert: the workflow transcript (per-member bubbles) **rehydrates** — the earlier turn is there.
  _(Guards workflow conversation attribution — the parent-run `user_id`/`session_id` path.)_

### Leg 20 — Memory RECALL: on vs off (proves save ≠ recall, both directions) ⭐
Needs a warm pod. Uses TWO agents to prove the distinction: memory ON recalls, memory OFF does
not — but OFF still SAVES.
- Precondition: a memory-**ON** agent `cic-journey-agent-mem` (seeded with memory enabled) + the
  memory-**OFF** `cic-journey-agent`.
- Do (memory ON): chat with `cic-journey-agent-mem` — turn 1 "my favorite number is 73", turn 2
  "what's my favorite number?".
- Assert (HARD): the reply says **73** — memory on → the agent recalled.
- Do (memory OFF): the same two turns with `cic-journey-agent`.
- Assert: the reply does **NOT** recall 73 (memory off → no recall) …
- Assert: … BUT the conversation is still **saved** — `cic-journey-agent`'s History shows both
  turns. (Save always; recall gated — the exact decouple.)

### Leg 21 — Consumer (marketplace) chat saves + rehydrates
Same guard as leg 5, on the production consumer surface (`CatalogChatPage`).
- Do: on `/catalog/<artifact>/chat`, send "the meeting is at noon".
- Do: leave + return (or reload) the consumer chat.
- Assert: "the meeting is at noon" **rehydrates** from the backend (consumer History).

### Leg 22 — Delete / cleanup through the UI (hard-delete, not deprecate)
- Do: delete `cic-journey-agent`, `cic-journey-agent2`, `cic-journey-agent-mem`, and
  `cic-journey-wf` via their UI delete controls.
- Assert: each is **gone** from its list (agents / workflows) — a real delete, not a lingering
  "deprecated" tombstone. Also confirm the seeded `cic-echo-tool` + `cic-journey-dataset` are gone.

---

## Further ideas (still open)
- [ ] **Safety scan** blocks a malicious input on the consumer surface — _intentionally left out
      for now (per request)._
- [ ] _add your own …_

## Notes — what I click, and what I won't
- **Authorized in-test actions — I DO perform these as written, without pausing to re-ask,
  because performing them IS the test:**
  - **Deploy** to sandbox / production (legs 3, 17, 9),
  - **Approve / Deny** in HITL — inline self-approve in sandbox (leg 12) AND approve in the
    reviewer console for production (leg 12b),
  - **Mark Version Passed**, **Mark Adversarial Passed**, and **Publish Agent** (leg 7),
  - **Promote to catalog** — pushing the artifact to the marketplace (leg 8),
  - **Delete** the created agents/workflow/tool/dataset (leg 22).
  Each of these is an explicit leg step above; treat every "Do: click …" as a green light to click
  the real control (Approve, Publish, Promote, Deploy, Delete) and continue.
- **What I still won't do:** reuse your existing tabs (I open a fresh one), read/alter browser
  history or saved credentials, type a password myself (login goes through the credential flow), or
  click any side-effecting control that is NOT one of the leg steps above (no ad-hoc/off-script
  actions).
- Each Assert produces a screenshot; the whole run can be captured as a GIF on request.
- Everything I create is cleaned up in **leg 22** (or left for your inspection if you prefer).
