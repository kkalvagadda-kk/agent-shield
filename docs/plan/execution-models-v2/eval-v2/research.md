# Eval v2 — Industry Survey: mode-aware / agentic / trajectory evaluation

**Companion to** `plan.md` and `data-model.md`. **Docs only — research inputs for the design.**

> ⚠️ **Plan status — design stable, specifics indicative.** The techniques and product capabilities
> surveyed here are cited and stable; the *mapping* to AgentShield primitives (§5) is design intent that
> WILL be re-grounded against live code when Eval v2 is minted into `tasks.md`. URLs verified during the
> 2026-07 survey; a few (flagged) came from search listings, not individual fetches.

Scope note: **trajectory eval** = scoring the *sequence of steps/tool-calls* an agent takes, not just the
final answer. **Reference-free** = judging with no golden/expected answer.

---

## 1. Why this survey exists

Today's judge (`services/registry-api/judge.py`) scores `input_text → output_text` only, and datasets
are `{input, expected_output}` text. That is **reactive-shaped evaluation**. Durable/scheduled/webhook
agents need trajectory, tool-call, side-effect, and filter-decision scoring. This survey establishes
what leading products/frameworks do so Eval v2 borrows proven patterns instead of inventing them.

**Headline finding:** deterministic **trajectory-match modes** (strict / unordered / subset / superset)
are the single most reusable primitive — pioneered by LangChain `agentevals` — and **deterministic
tool-call scoring** exists in Ragas + DeepEval. Everyone else is LLM/SLM-judge-based. **No product ships
tool side-effect mocking/record-replay as an in-metric primitive** — that is a gap Eval v2 must fill
itself (the sandbox record seam, `data-model.md` §4). Likewise, **event-driven "judge the routing/filter
decision AND the action"** has no off-the-shelf benchmark — compose a classification metric on the
filter decision with trajectory-match on the action.

---

## 2. Per-product capabilities

### LangSmith / LangChain (`openevals` + `agentevals`) — the deterministic trajectory reference
- **First-class trajectory matching:** `create_trajectory_match_evaluator` with four
  `trajectory_match_mode` values — `strict`, `unordered`, `subset`, `superset` — against a reference
  trajectory of OpenAI-style messages. https://github.com/langchain-ai/agentevals
- **Tunable tool-call scoring:** `tool_args_match_mode` (`exact`/`ignore`/`subset`/`superset`) +
  per-tool `tool_args_match_overrides`; `create_json_match_evaluator` scores tool/extraction JSON
  field-by-field. https://github.com/langchain-ai/agentevals
- **Reference-free trajectory judge:** `create_trajectory_llm_as_judge` (built-in
  `TRAJECTORY_ACCURACY_PROMPT`) scores logical progression / tool appropriateness with no golden path;
  openevals ships reference-free prompts (`CONCISENESS_PROMPT`, `HALLUCINATION_PROMPT`,
  `RAG_GROUNDEDNESS_PROMPT`). https://github.com/langchain-ai/openevals/blob/main/README.md
- **Simulation + mock tools:** `run_multiturn_simulation` + `create_llm_simulated_user`; documented mock
  tool environments injecting failures/timeouts/malformed responses, pytest/CI gating.
  https://www.langchain.com/langsmith/evaluation

### Braintrust (`autoevals` + `Eval()`) — flexible harness, no agent primitives
- **`Eval(name, {data, task, scores})`** core primitive; `task` can be a multi-step agent or pipeline;
  `data` records are arbitrary payloads. https://www.braintrust.dev/docs/guides/evals
- **Mixed scorers:** reference-free (`ClosedQA`, `Moderation`, `Security`), reference-based
  (`Factuality`, `Levenshtein`, `ExactMatch`, `JSONDiff`), custom `LLMClassifier.from_template`.
  https://github.com/braintrustdata/autoevals
- **No trajectory or tool-sequence scorer, no tool mocking** — make the agent the `task`, write a custom
  scorer. Same repo.

### Langfuse — observability-first (evaluate recorded traces)
- **LLM-as-judge on Observations/Traces/Experiments;** a **"Tool Calls" mapping** exposes recorded tool
  calls (full array or JSONPath `$[*].name`) to the judge.
  https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge
- **Reference-free by default;** maps `input`/`output`/optional `ground_truth`; managed evaluators
  (hallucination, context-relevance, toxicity). Same URL.
- **Arbitrary payload input + Scores API** — mapped `input` can be an event/job body; external
  evaluators push back via `langfuse.create_score(...)`. *(listing)*
- **Directly relevant:** AgentShield already writes Langfuse traces + judge scores. The "map recorded
  tool-calls into a judge via JSONPath" pattern is the lowest-friction trajectory read for us.

### OpenAI Evals — YAML harness + Graders API
- **Registry/YAML model-graded evals;** custom classes subclass `evals.Eval`, override `eval_sample()`.
  https://github.com/openai/evals/blob/main/docs/build-eval.md
- **`CompletionFn` wraps a whole system** (tool-using agent / pipeline) — offline replay, not a live
  mock. https://github.com/openai/evals/blob/main/docs/completion-fns.md
- **`multi` grader scores a tool call by function name + arguments separately** (the canonical tool-call
  grading example); `string_check`/`text_similarity` reference-based, `score_model`/`python`
  reference-free. No first-class trajectory eval.
  https://developers.openai.com/api/docs/guides/graders

### Humanloop — evaluator over a Log (Code / AI / Human) *(being sunset post-acquisition; docs live)*
- **Evaluator = function over a Log;** the Log carries the full trajectory incl. tool calls.
  https://humanloop.com/docs/explanation/evaluators
- **Reference-free first-class** (AI Evaluator is a Prompt reading `{{ log.… }}`, testcase optional);
  **Online (live Logs) vs Offline (Dataset)** modes. https://humanloop.com/docs/guides/evals/llm-as-a-judge

### Arize Phoenix — most explicitly agent/trajectory-oriented observability tool
- **Dedicated tool-calling eval** scoring tool *selection* + *parameter extraction* from `question` +
  `tool_call` + `tool_definitions`; legacy `TOOL_CALLING_TEMPLATE` splitting into
  `ToolSelectionEvaluator` / `ToolInvocationEvaluator` *(signatures inferred from cookbook)*.
  https://arize.com/docs/phoenix/evaluation/running-pre-tested-evals/tool-calling-eval
- **Trajectory + convergence evals:** ordered tool-call spans judged as a whole; **convergence** compares
  step count to the known minimum (efficiency).
  https://arize.com/docs/phoenix/cookbook/evaluation/evaluate-an-agent
- **`run_experiment` / `evaluate_experiment`;** tool-call eval judges against tool defs, no ground truth
  needed. Same URL.

### Ragas — deterministic tool metrics + LLM goal/topic metrics (`MultiTurnSample`)
- **`ToolCallAccuracy` (deterministic)** — actual vs expected tool sequence + args; `strict`/`flexible`
  order; score = arg-accuracy × sequence-alignment. **`ToolCallF1`** — unordered precision/recall/F1.
  https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/
- **`AgentGoalAccuracy`** — LLM-judge, with-reference (end-state vs ideal) **or without-reference**
  (infers goal). **`TopicAdherenceScore`** — precision/recall/F1 on staying in-domain (= correct
  routing/filtering). Same URL.
- **`AspectCritic`** — reference-free binary yes/no from free-text criteria (majority vote); siblings
  `RubricsScore`, `SimpleCriteriaScore`.
  https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/general_purpose/

### DeepEval — layered reasoning/action/execution metrics
- **`ToolCorrectnessMetric` (deterministic)** — name-match `tools_called` vs `expected_tools`;
  `should_consider_ordering`, `should_exact_match`, `evaluation_params=[INPUT_PARAMETERS, OUTPUT]`.
  https://deepeval.com/docs/metrics-tool-correctness
- **`ArgumentCorrectnessMetric` (LLM, reference-free)** — are the tool-call params appropriate given
  `input`. https://deepeval.com/docs/metrics-argument-correctness
- **`TaskCompletionMetric` (LLM, reference-free)** — analyzes the full `@observe` trace to judge success.
  https://deepeval.com/docs/metrics-task-completion
- **G-Eval + DAG** — custom judges (criteria→CoT score) and deterministic decision-tree of LLM
  judgments. https://deepeval.com/guides/guides-ai-agent-evaluation

### Galileo — node-typed agentic metrics (Luna-2 SLM judges)
- **Node-typed metrics:** *Action Advancement* / *Action Completion* / *Tool Selection Quality* / *Tool
  Error* / *Agent Flow* (validates the whole trajectory vs NL tests).
  https://docs.galileo.ai/concepts/metrics/agentic/agentic-overview
- **Luna-2** — fine-tuned single-token SLM judges, 20+ metrics at once, sub-200ms real-time guardrails.
  https://galileo.ai/luna-2 *(listing)*

### Patronus AI — trace/trajectory debugging + benchmarks
- **Percival** — adaptive agent debugger over full traces; 20+ failure modes (TRAIL taxonomy).
  https://www.patronus.ai/agents
- **TRAIL benchmark** — "Trace Reasoning and Agentic Issue Localization"; 20+ error types (incl.
  tool-call errors, timeouts); 148 annotated long-context traces; SOTA <11%.
  https://www.patronus.ai/blog/introducing-trail-a-benchmark-for-agentic-evaluation
- **Lynx** (hallucination vs `retrieved_context`) + custom Judge/GLIDER guardrails (task completion,
  control-flow order, tool appropriateness). https://docs.patronus.ai/docs/evaluation_api/lynx *(listing)*

---

## 3. Comparison table

| Product | Trajectory eval | Tool-call scoring | Reference-free | Side-effect / mock | Event/payload input | Notable primitive |
|---|---|---|---|---|---|---|
| **LangSmith / agentevals** | Yes — 1st-class, 4 match modes | Yes — deterministic + arg-match modes | Yes — trajectory LLM-judge | Mock tool envs + user sim (no record/replay of own effects) | Indirect (dataset payloads) | `create_trajectory_match_evaluator`, `create_trajectory_llm_as_judge` |
| **Braintrust** | No built-in | Custom scorer only | Yes (mixed) | No native primitive | Yes — arbitrary `data` | `Eval()`, `LLMClassifier`, `Factuality` |
| **Langfuse** | Recorded trace + LLM-judge | Yes — "Tool Calls" mapping (judge) | Yes (managed evaluators) | Records real tools; no mock | Yes — any field via JSONPath | LLM-judge on Observations, `create_score` |
| **OpenAI Evals** | No 1st-class | Yes — `multi` grader (name+args) | Yes — `score_model`/`python` | No; `CompletionFn` wraps system | JSONL samples | `CompletionFn`, `multi` grader |
| **Humanloop** | Implicit (full Log) | Author-your-own over Log | Yes — target optional | Not documented | Log/Datapoint | Evaluator (Code/AI/Human), Online/Offline |
| **Arize Phoenix** | Yes — ordered spans + convergence | Yes — ToolSelection/ToolInvocation | Yes — judged vs tool defs | Recorded traces; no mock | OTEL spans | `run_experiment`, convergence eval |
| **Ragas** | Yes — multi-turn tool sequences | Yes — **deterministic** (`ToolCallAccuracy`/`F1`) | Yes — `AspectCritic`, goal-no-ref | No (consumes recorded ToolCalls) | `MultiTurnSample` | `AspectCritic`, `ToolCallAccuracy`, `AgentGoalAccuracy` |
| **DeepEval** | Yes — trace via `@observe` | **Both** deterministic + LLM | Yes — `TaskCompletion`, G-Eval | No mock harness | `LLMTestCase` / `@observe` | `ToolCorrectnessMetric`, `TaskCompletionMetric`, DAG |
| **Galileo** | Yes — Agent Flow (NL tests) | Semantic (`Tool Selection Quality`) | Yes | Not documented | Node-typed spans | Luna-2 SLM judges, Agent Flow |
| **Patronus** | Yes — Percival + TRAIL | Semantic/taxonomic | Yes (Lynx, judges) | Generative Simulators (separate) | Evaluate-API + traces | Percival, TRAIL, Lynx |

**Pattern:** deterministic strict/unordered/subset/superset trajectory matching is **unique to
`agentevals`**; deterministic tool-call scoring otherwise only in Ragas + DeepEval; everyone else is
judge-based. **No product exposes tool-side-effect mock/record-replay as an in-metric primitive.**

---

## 4. Techniques (with citations)

### 4.1 LLM-as-judge best practices & bias mitigation
- **Canonical judge paper — MT-Bench / Chatbot Arena (Zheng et al., NeurIPS 2023):** a strong judge
  (GPT-4) reaches >80% agreement with humans — the level humans agree with each other — and names
  **position, verbosity, self-enhancement** bias. https://arxiv.org/abs/2306.05685
- **Mitigations (same):** swap answer order, count a win only if consistent across both orderings;
  few-shot / reference-guided grading; add a reference answer for math/reasoning.
  https://arxiv.org/html/2306.05685v4
- **G-Eval (Liu et al. 2023)** — reference-free rubric scoring: give criteria → auto-generate CoT eval
  steps → form-filling/weighted score; Spearman 0.514 vs humans, beating BLEU/ROUGE.
  https://arxiv.org/abs/2303.16634
- **Bias survey — "Justice or Prejudice?" (2024):** CALM framework quantifies **12 judge bias types**;
  persist even in strong models. https://arxiv.org/abs/2410.02736 · self-preference:
  https://arxiv.org/abs/2410.21819

### 4.2 Trajectory & tool-use evaluation
- **agentevals trajectory-match modes** — `strict` (same calls, same order), `unordered` (same calls,
  any order), `superset` (actual ⊇ reference), `subset` (actual ⊆ reference).
  https://github.com/langchain-ai/agentevals
- **Exact vs semantic tool-arg scoring** — `tool_args_match_mode` = `exact`/`ignore`/subset/superset +
  per-tool custom matchers; LangSmith's framing: deterministic match for well-defined workflows,
  LLM-judge for open-ended. https://docs.langchain.com/langsmith/trajectory-evals
- **Outcome- vs path-based (τ-bench, Yao et al. 2024)** — compares final **database state** to an
  annotated goal state instead of matching the path; introduces **pass^k** for reliability across
  trials. https://arxiv.org/abs/2406.12045

### 4.3 Evaluating side-effecting / agentic runs (mock, sandbox, replay)
- **τ-bench** — agent runs against domain-API tools backed by a **real DB in a sandbox**; correctness =
  DB-state assertion at the end (with a simulated user). https://arxiv.org/abs/2406.12045
- **WebArena (Zhou et al. 2023)** — four self-hosted web apps as a reproducible sandbox; each of 812
  tasks has a **programmatic functional-correctness check** on resulting state.
  https://arxiv.org/abs/2307.13854
- **LangSmith mock tool environments** — simulated env returns fake data; inject failures / malformed
  responses / rate-limits / timeouts; pytest/CI gating on score regressions.
  https://www.langchain.com/langsmith/evaluation

### 4.4 Evaluating event-driven / triggered systems (routing + action) — **thinnest literature**
No purpose-built benchmark for "event → judge the routing/filter decision **and** the downstream action"
exists. **Compose** a classification metric on the routing decision with trajectory-match on the action.
- **RouterBench (Hu et al. 2024)** — routing benchmark, but scores **multi-LLM model routing** on
  performance-vs-cost, not event/intent routing. https://arxiv.org/abs/2403.12031
- **Ragas decision+action metrics** — `tool_call_accuracy` (right tool/args), `agent_goal_accuracy`
  (path achieved goal), `topic_adherence` (stayed in allowed domain = correct filtering).
  https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/agents/
- Practitioner framing of intent-classification as a distinct evaluable router layer (industry,
  non-peer-reviewed): https://tianpan.co/blog/2026-04-16-intent-classification-agent-routers

### 4.5 Untrusted-input / prompt-injection robustness eval
- **AgentDojo (Debenedetti et al. 2024)** — 97 realistic tasks + 629 security cases; the two-axis
  tradeoff: **task utility vs attack success rate (ASR)** (ASR <25% vs best agents; detector defense →
  ~8%). https://arxiv.org/abs/2406.13352
- **InjecAgent (Zhan et al., ACL Findings 2024)** — indirect prompt injection: 1,054 cases, 17 user / 62
  attacker tools; direct-harm + data-exfiltration intents; ReAct GPT-4 attacked ~24%, ~2× with a
  reinforcing "hacking prompt." https://arxiv.org/abs/2403.02691
- Both report **ASR** and **utility separately**, so a defense that tanks utility to cut ASR is visibly
  penalized — the right shape for measuring AgentShield's OPA/HITL defense cost.

### 4.6 Multi-turn / conversational eval
- **MT-Bench multi-turn** — two-turn questions, judge scores turn 2 given turn 1; single-turn skill
  doesn't predict multi-turn. https://arxiv.org/abs/2306.05685
- **MINT (Wang et al., ICLR 2024)** — multi-turn interaction with tools + NL feedback; better single-turn
  ≠ better multi-turn. https://arxiv.org/abs/2309.10691
- **DeepEval conversational metrics** — Completeness/Relevancy, Role Adherence, Knowledge Retention,
  Conversational G-Eval, `ConversationSimulator`. https://deepeval.com/docs/metrics-introduction
- **Ragas multi-turn** — metrics extend `MultiTurnMetric`; `AspectCritic` binary over a full
  conversation; `topic_adherence` across turns.
  https://docs.ragas.io/en/stable/howtos/applications/evaluating_multi_turn_conversations/

---

## 5. Mapping to AgentShield primitives (design intent)

| Industry pattern | AgentShield mechanism | Notes |
|---|---|---|
| agentevals trajectory-match modes | `score_trajectory(match_mode ∈ exact/ordered/superset/unordered)` over `run_steps` | our `run_steps` (name/status/output) are the trajectory; match modes rename LangChain's set (`ordered`≈`strict`). |
| Ragas/DeepEval deterministic tool-call | `score_tool_calls` (name exact + args partial/semantic) — **code, not LLM** | reserve LLM for arg *appropriateness* (a later scorer). |
| τ-bench / WebArena final-state assertion | `expected_side_effects` asserted vs **recorded** tool calls (the `eval_mode=record` seam) | no product ships this in-metric; it doubles as our safe replay harness. |
| Ragas `topic_adherence` + trajectory | webhook eval: `score_filter` (match/miss vs `AgentEvent.status`) + `score_trajectory` on the action | fills the event-routing gap the literature leaves open. |
| AgentDojo/InjecAgent ASR + utility | `score_injection` (`must_not_call` + `must_refuse`) reported alongside task utility | measures the OPA/HITL defense cost, not just utility. |
| G-Eval / `AspectCritic` reference-free | `rubric` on any item; `score_response` runs reference-free when no `expected_output` | table-stakes for agentic runs with no golden answer. |
| MT-Bench bias mitigation | position-swap-consistency + verbosity guardrails baked into `score_response` | defaults, not opt-ins. |
| Langfuse "map tool-calls into judge" | judge reads `run_steps`/trace tool-calls directly | reuses existing instrumentation; lowest-friction. |

---

## 6. Top references leaned on

1. **LangChain `agentevals`** (trajectory-match modes + tool-arg match modes) —
   https://github.com/langchain-ai/agentevals — the backbone of our trajectory/tool-call scorers.
2. **τ-bench (Yao et al. 2024)** (sandboxed final-state assertion + pass^k) —
   https://arxiv.org/abs/2406.12045 — the model for our side-effect record/assert seam.
3. **MT-Bench / LLM-as-judge (Zheng et al. 2023)** (judge validity + bias mitigation) —
   https://arxiv.org/abs/2306.05685 — the basis for our judge hardening.

(Honorable mentions actively used: Ragas `ToolCallAccuracy`/`AspectCritic`, DeepEval
`ToolCorrectness`/`TaskCompletion`, AgentDojo/InjecAgent for the injection axis.)

### Sourcing caveats
Humanloop is being sunset post-acquisition (docs live). Phoenix's
`ToolSelectionEvaluator`/`ToolInvocationEvaluator` signatures are inferred from the cookbook, not a
dedicated API page. A few Patronus/Langfuse/DeepEval sub-pages were confirmed via search listings, not
individual fetches (flagged inline). The event-driven/triggered group (§4.4) is genuinely thin — treat
it as a design gap we are filling, not a solved problem.
