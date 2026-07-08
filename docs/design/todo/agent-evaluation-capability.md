# Agent Evaluation Capability — Product Research & Requirements

**Status**: RESEARCH — Product requirements definition  
**Date**: 2026-07-07  
**Author**: Karthik + Claude

## Context

AgentShield currently has a basic eval system: single-dimension LLM-as-Judge (Haiku scoring 0.0–1.0 on "response quality"), a batch eval-runner K8s Job, datasets (input/expected_output pairs), and a pass/fail threshold (0.7) gating publish. This is functional but shallow compared to what the market offers and what governed agents actually need.

This document synthesizes research across **Langfuse, Braintrust, LangSmith, DeepEval, Arize Phoenix, and HumanLoop** to define WHAT AgentShield's eval capability should become.

---

## 1. Market Landscape — What Platforms Offer

### Evaluation Dimensions (from DeepEval's 50+ metrics)

| Category | Metrics | Who Has It |
|----------|---------|-----------|
| **Agent-Specific** | Task Completion, Tool Correctness, Argument Correctness, Step Efficiency, Plan Adherence, Plan Quality | DeepEval |
| **RAG/Retrieval** | Contextual Relevancy, Contextual Precision, Contextual Recall, Faithfulness, Answer Relevancy | DeepEval, Langfuse (RAGAS), Braintrust |
| **Safety** | Bias, Toxicity, PII Leakage, Role Violation, Misuse, Non-Advice | DeepEval |
| **Conversation** | Knowledge Retention, Role Adherence, Conversation Completeness, Conversation Relevancy | DeepEval |
| **General** | Hallucination, Factuality, Summarization, JSON Correctness | All platforms |

### Evaluation Execution Models

| Model | Description | Who |
|-------|-------------|-----|
| **Online (production)** | Auto-score live traces as they come in, no reference output needed | Langfuse, Braintrust, LangSmith |
| **Offline (batch)** | Run against curated datasets, compare versions | All |
| **Interactive (playground)** | Quick iteration on small sets, immediate feedback | Langfuse, Braintrust, LangSmith |
| **CI/CD gate** | Block deploys on regression | Braintrust, LangSmith |

### Scorer Types

| Type | Description | Who |
|------|-------------|-----|
| **LLM-as-Judge** | Prompted LLM rates output | All |
| **Code/Heuristic** | Deterministic rules (regex, token count, JSON schema) | Langfuse, Braintrust, LangSmith |
| **Human Annotation** | Structured review queues with rubrics | Langfuse, LangSmith |
| **Pairwise/Comparative** | A vs B preference ranking | LangSmith, Braintrust |
| **Custom composite** | User-defined evaluation logic | All |

### Dataset Management

| Feature | Who |
|---------|-----|
| Versioned datasets with history | Langfuse |
| Production traces → dataset items | Langfuse, Braintrust |
| Golden datasets with schema validation | Langfuse |
| Multi-modal support (images, audio) | Langfuse |
| CSV/JSON import | All |

### Experiment Management

| Feature | Who |
|---------|-----|
| Immutable experiment snapshots | Braintrust |
| Side-by-side version comparison | All |
| Regression detection across experiments | Braintrust, LangSmith |
| Hill-climbing (prev output = next expected) | Braintrust |
| Trial-based variance measurement | Braintrust |

---

## 2. What AgentShield ALREADY Has (Current State)

| Capability | Status |
|-----------|--------|
| Single-judge LLM scorer (response quality 0-1) | Done |
| Batch eval runner (K8s Job per dataset) | Done |
| Datasets (input + expected_output) | Done |
| Per-item results with score + reasoning | Done |
| Eval gating publish (score ≥ 0.7 → eval_passed) | Done |
| Langfuse trace linkage per eval item | Done |
| Workflow evaluation (trigger + poll) | Done |

### Gaps vs Market

1. **Single dimension only** — no tool correctness, safety, faithfulness
2. **No human annotation workflow** — thumbs-up/down exists in playground but no structured review queues
3. **No online/production evaluation** — evals only run on-demand against datasets
4. **No code/heuristic evaluators** — everything goes through LLM judge
5. **No experiment comparison** — can't compare version A vs B systematically
6. **No regression detection** — no CI/CD integration, no "score dropped" alerts
7. **No dataset versioning** — items are flat, no snapshots
8. **No custom evaluator authoring** — users can't define their own judge prompts
9. **No agent-specific metrics** — tool selection accuracy, trajectory evaluation absent
10. **No pairwise evaluation** — can't rank two versions against each other

---

## 3. WHAT AgentShield Should Build — Capability Requirements

### Tier 1: Core Eval Engine (Must-Have for Governed Agents)

#### 1.1 Multi-Dimension Scoring
- **What**: Each eval run produces scores across N configurable dimensions, not one.
- **Dimensions needed for governed agents**:
  - Response Quality (exists today)
  - **Tool Correctness** — did agent pick the right tool? correct arguments?
  - **Safety Compliance** — did agent violate any policy? PII leak? role violation?
  - **Task Completion** — did agent actually accomplish what was asked?
  - **Faithfulness** — is response grounded in retrieved context (no hallucination)?
- **Why AgentShield specifically**: Our agents have governance (OPA + HITL). Eval MUST validate that governance behaves correctly under adversarial inputs, not just that output reads well.

#### 1.2 Custom Evaluator Definitions
- **What**: Users define evaluator = (prompt template + scoring rubric + target dimension).
- **Types to support**:
  - LLM-as-Judge (custom prompt with {{input}}, {{output}}, {{expected}}, {{tool_calls}} variables)
  - Code evaluator (Python function returning score + reason)
  - Composite (weighted combination of other evaluators)
- **Why**: Different agents need different quality bars. A customer-facing chatbot vs an internal data pipeline agent have completely different eval criteria.

#### 1.3 Evaluator Marketplace / Library
- **What**: Platform-managed set of evaluator templates users can fork.
- **Starter set**: Response Quality, Tool Correctness, Safety (PII + Toxicity), Faithfulness, Task Completion, Role Adherence.
- **Why**: Most teams won't write evaluators from scratch. Templates lower adoption friction.

#### 1.4 Agent Trajectory Evaluation
- **What**: Evaluate the SEQUENCE of steps an agent took, not just final output.
- **Assess**: Were intermediate tool calls appropriate? Did agent loop unnecessarily? Did it respect plan ordering?
- **Why AgentShield specifically**: Our workflows have explicit edges (sequential, conditional, supervisor, handoff). Eval should validate the agent followed the intended graph, not just that it produced good text.

---

### Tier 2: Experiment & Comparison (Must-Have for Iteration)

#### 2.1 Version Comparison Experiments
- **What**: Run same dataset against version A and version B; show per-item and aggregate score diffs.
- **UI**: Side-by-side results table with green/red delta indicators.
- **Why**: Currently users have no way to know if a prompt change improved or regressed quality without manual effort.

#### 2.2 Regression Detection
- **What**: When a new eval run completes, auto-compare against previous best run for same agent+dataset. Flag if any dimension dropped > threshold.
- **Alerts**: In-app notification + optional webhook.
- **Why**: Prevents silent quality degradation during rapid iteration.

#### 2.3 Experiment History & Snapshots
- **What**: Each eval run is an immutable experiment. Show score trends over time per agent.
- **Chart**: Line chart of dimension scores across versions/runs.
- **Why**: Teams need to see trajectory — "are we improving?" — not just point-in-time scores.

---

### Tier 3: Production Evaluation (Should-Have)

#### 3.1 Online Evaluation (Score Live Traffic)
- **What**: Automatically score a sample of production runs using configured evaluators. No dataset needed — the live trace IS the input.
- **Sampling**: Configurable rate (e.g., 10% of production runs).
- **Why**: Playground evals don't catch distribution shift. Production traffic brings edge cases you never imagined in a dataset.

#### 3.2 Human Annotation Queues
- **What**: Structured review interface where team members rate agent outputs on rubrics. Annotation queue = filtered set of runs needing review.
- **Features**: Multi-reviewer support, inter-annotator agreement tracking, rubric customization.
- **Why**: LLM judges have known failure modes. Human oversight is mandatory for high-stakes governed agents.

#### 3.3 Production → Dataset Pipeline
- **What**: One-click "add to dataset" from any production run or annotation queue item. Automatically builds regression test sets from real-world failures.
- **Why**: Best datasets are curated from production failures, not imagined in a spreadsheet.

---

### Tier 4: Governance-Specific Evaluation (AgentShield Differentiator)

#### 4.1 Safety Eval Suite
- **What**: Pre-built adversarial dataset + evaluators specifically testing governance:
  - Prompt injection attempts → agent should refuse
  - PII extraction attempts → agent should redact
  - Policy-violating requests → OPA should block
  - HITL trigger scenarios → approval should fire
- **Why**: No other platform has this. AgentShield's value prop IS governance — our eval must prove it works.

#### 4.2 Governance Behavior Regression
- **What**: Suite that runs automatically on every agent version change, testing that governance policies still fire correctly.
- **Assertion types**: "OPA blocked this", "HITL triggered", "PII was redacted", "tool was denied".
- **Why**: A governance platform that can't prove its governance works under adversarial conditions is a liability.

#### 4.3 Red Team Evaluation
- **What**: Automated red-teaming — generate adversarial inputs targeting the agent's specific tools and capabilities, run eval, report attack surface.
- **Why**: Proactive security validation. The market lacks self-hosted red-team automation for governed agents.

---

### Tier 5: Infrastructure & Platform Capabilities

#### 5.1 Dataset Management Enhancements
- Dataset versioning (snapshots, compare across versions)
- Tags and filtering (by difficulty, category, failure mode)
- Auto-generation from production traces
- Schema validation for structured datasets

#### 5.2 CI/CD Integration
- Webhook/API trigger for eval runs from CI pipelines
- Exit code / status for pass/fail gating
- GitHub/GitLab check integration

#### 5.3 Eval Analytics Dashboard
- Score distributions per dimension
- Score trends over time (per agent, per version)
- Failure clustering (which types of inputs consistently fail?)
- Cost tracking (eval LLM spend)

#### 5.4 Multi-Turn / Conversation Evaluation
- Evaluate full conversation sessions, not just single turns
- Metrics: knowledge retention, conversation coherence, goal completion across turns

---

## 4. Prioritization Recommendation (PM View)

| Priority | Capability | Rationale |
|----------|-----------|-----------|
| **P0** | Multi-dimension scoring (1.1) | Foundation for everything else |
| **P0** | Custom evaluator definitions (1.2) | Unlocks per-agent eval configuration |
| **P0** | Safety eval suite (4.1) | AgentShield's core differentiator |
| **P1** | Agent trajectory evaluation (1.4) | Workflow correctness matters for governed agents |
| **P1** | Version comparison (2.1) | Essential iteration feedback loop |
| **P1** | Governance behavior regression (4.2) | Proves the platform works |
| **P1** | Evaluator library (1.3) | Adoption friction reducer |
| **P2** | Regression detection (2.2) | Quality guard rails |
| **P2** | Online production evaluation (3.1) | Catches drift |
| **P2** | Human annotation queues (3.2) | Governance requires human oversight |
| **P2** | Experiment history (2.3) | Visibility into improvement trajectory |
| **P3** | Production → dataset pipeline (3.3) | Virtuous cycle for dataset curation |
| **P3** | CI/CD integration (5.2) | DevOps maturity |
| **P3** | Red team evaluation (4.3) | Advanced security posture |
| **P3** | Multi-turn eval (5.4) | Conversational agent quality |
| **P3** | Dataset versioning (5.1) | Data management maturity |
| **P3** | Eval analytics dashboard (5.3) | Visibility at scale |

---

## 5. Competitive Positioning

| Platform | Strength | AgentShield's Edge |
|----------|----------|-------------------|
| Langfuse | Best tracing + managed evaluators + dataset mgmt | Already integrated; extend, don't replace |
| Braintrust | Best experiment management + regression detection | We add governance-aware eval they can't |
| DeepEval | Richest metric catalog (50+) | We focus on governed-agent metrics they lack |
| LangSmith | Tightest LangChain integration | We're framework-agnostic |
| All of them | Generic LLM eval | None evaluate governance behavior (OPA, HITL, safety policies) |

**AgentShield's unique position**: The only platform where evaluation and governance are the same system. We don't just score "was the output good?" — we score "did the agent respect its boundaries?"

---

## 6. Sources & References

- [Langfuse Scores/Evaluation](https://langfuse.com/docs/scores/overview) — scoring types, model-based evals, annotation queues
- [Langfuse Tracing](https://langfuse.com/docs/tracing) — trace architecture for agents
- [Langfuse Datasets](https://langfuse.com/docs/datasets/overview) — versioning, experiments, golden datasets
- [Braintrust Evals Guide](https://www.braintrust.dev/docs/guides/evals) — experiments, online scoring, CI/CD
- [Braintrust Eval Authoring](https://www.braintrust.dev/docs/guides/evals/write) — scorer types, hill climbing, regression
- [LangSmith Evaluation Concepts](https://docs.langchain.com/langsmith/evaluation-concepts) — agent trajectory, tool correctness, annotation queues
- [DeepEval Metrics](https://deepeval.com/docs/metrics-introduction) — 50+ metrics catalog including agent-specific
- [HumanLoop Blog: Evaluating LLM Apps](https://www.humanloop.com/blog/evaluating-llm-apps) — 3-level eval hierarchy, judgment types
- [Hamel Husain: Evals](https://hamel.dev/blog/posts/evals/) — practical eval system design, unit→human→A/B hierarchy
