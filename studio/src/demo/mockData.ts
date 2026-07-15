// Mock data for the UX preview. Purely illustrative — no backend.

// ── Legacy agents (so the landing page / sidebar feel alive) ────────────────
export const MOCK_AGENTS = [
  { id: "a1", name: "refund-assistant", team: "commerce", description: "Handles refund requests end to end", status: "active", agent_type: "declarative", publish_status: "published", agent_class: "user_delegated", execution_shape: "durable", memory_enabled: true, created_at: "2026-07-01T10:00:00Z", updated_at: "2026-07-10T10:00:00Z", created_by: "demo", metadata: {}, latest_version_number: 3 },
  { id: "a2", name: "fraud-checker", team: "risk", description: "Flags suspicious transactions", status: "active", agent_type: "declarative", publish_status: "draft", agent_class: "daemon", execution_shape: "reactive", memory_enabled: false, created_at: "2026-07-02T10:00:00Z", updated_at: "2026-07-09T10:00:00Z", created_by: "demo", metadata: {}, latest_version_number: 1 },
  { id: "a3", name: "policy-qa", team: "platform", description: "Answers policy questions from the Knowledge Base", status: "active", agent_type: "declarative", publish_status: "published", agent_class: "user_delegated", execution_shape: "reactive", memory_enabled: true, created_at: "2026-07-03T10:00:00Z", updated_at: "2026-07-11T10:00:00Z", created_by: "demo", metadata: {}, latest_version_number: 5 },
];

// ── Knowledge Base ──────────────────────────────────────────────────────────
export type IngestStatus = "queued" | "processing" | "ready" | "failed";

export interface KBSource {
  id: string;
  name: string;
  type: "pdf" | "txt" | "md" | "docx";
  sizeKb: number;
  chunks: number;
  status: IngestStatus;
  error?: string;
  addedBy: string;
  addedAt: string;
}

export interface KnowledgeBase {
  id: string;
  name: string;
  team: string;
  description: string;
  embeddingModel: string;
  sources: KBSource[];
  updatedAt: string;
  attachedAgents: string[];
}

export const MOCK_KBS: KnowledgeBase[] = [
  {
    id: "kb-policies",
    name: "Company Policies",
    team: "platform",
    description: "Refund, security, travel and conduct policies used by support agents.",
    embeddingModel: "titan-embed-text-v2 (default)",
    updatedAt: "2026-07-14T09:12:00Z",
    attachedAgents: ["policy-qa", "refund-assistant"],
    sources: [
      { id: "s1", name: "refund-policy.pdf", type: "pdf", sizeKb: 214, chunks: 12, status: "ready", addedBy: "demo", addedAt: "2026-07-10T08:00:00Z" },
      { id: "s2", name: "security-policy.pdf", type: "pdf", sizeKb: 508, chunks: 26, status: "ready", addedBy: "demo", addedAt: "2026-07-10T08:05:00Z" },
      { id: "s3", name: "travel-policy.docx", type: "docx", sizeKb: 96, chunks: 0, status: "processing", addedBy: "demo", addedAt: "2026-07-14T09:10:00Z" },
      { id: "s4", name: "code-of-conduct.md", type: "md", sizeKb: 18, chunks: 0, status: "failed", error: "Embedding call timed out — retryable.", addedBy: "demo", addedAt: "2026-07-14T09:11:00Z" },
    ],
  },
  {
    id: "kb-product",
    name: "Product Docs",
    team: "platform",
    description: "Public product documentation for the assistant to ground answers on.",
    embeddingModel: "titan-embed-text-v2 (default)",
    updatedAt: "2026-07-13T14:00:00Z",
    attachedAgents: ["policy-qa"],
    sources: [
      { id: "s5", name: "getting-started.md", type: "md", sizeKb: 42, chunks: 9, status: "ready", addedBy: "demo", addedAt: "2026-07-12T10:00:00Z" },
      { id: "s6", name: "api-reference.pdf", type: "pdf", sizeKb: 1220, chunks: 64, status: "ready", addedBy: "demo", addedAt: "2026-07-12T10:10:00Z" },
      { id: "s7", name: "faq.txt", type: "txt", sizeKb: 11, chunks: 5, status: "ready", addedBy: "demo", addedAt: "2026-07-13T13:55:00Z" },
    ],
  },
  {
    id: "kb-runbooks",
    name: "Support Runbooks",
    team: "support",
    description: "Internal runbooks for common support scenarios.",
    embeddingModel: "titan-embed-text-v2 (default)",
    updatedAt: "2026-07-08T11:00:00Z",
    attachedAgents: [],
    sources: [
      { id: "s8", name: "escalation.md", type: "md", sizeKb: 27, chunks: 7, status: "ready", addedBy: "demo", addedAt: "2026-07-08T10:00:00Z" },
      { id: "s9", name: "outage-playbook.pdf", type: "pdf", sizeKb: 340, chunks: 18, status: "ready", addedBy: "demo", addedAt: "2026-07-08T10:30:00Z" },
    ],
  },
];

export interface Chunk {
  index: number;
  text: string;
  tokens: number;
}

// Chunks shown in the chunk viewer for refund-policy.pdf.
export const MOCK_CHUNKS: Chunk[] = [
  { index: 0, text: "Refund eligibility: Customers may request a full refund within 30 days of purchase, provided the item is unused and in its original packaging.", tokens: 34 },
  { index: 1, text: "Refunds for digital goods are only issued if the product was not downloaded or accessed. Partial refunds are not offered for digital goods.", tokens: 31 },
  { index: 2, text: "High-value refunds above $500 require secondary approval from a team lead before processing. The approval must be recorded in the case notes.", tokens: 33 },
  { index: 3, text: "Refunds are issued to the original payment method within 5–7 business days. Store credit may be offered as an alternative at the customer's request.", tokens: 36 },
];

// Test-retrieval results keyed loosely by query intent.
export interface RetrievalHit {
  score: number;
  source: string;
  text: string;
}
export const MOCK_RETRIEVAL: RetrievalHit[] = [
  { score: 0.91, source: "refund-policy.pdf", text: "High-value refunds above $500 require secondary approval from a team lead before processing." },
  { score: 0.87, source: "refund-policy.pdf", text: "Customers may request a full refund within 30 days of purchase, provided the item is unused." },
  { score: 0.72, source: "security-policy.pdf", text: "All transactions over $10k require dual approval and are logged for audit." },
];

// ── Multi-agent conversation (shared workflow thread) ───────────────────────
export interface Citation {
  source: string;
  kb: string;
}
export interface AgentTurn {
  author: string;          // "user" | agent name
  role: "user" | "agent";
  color: string;           // tailwind text color for the label
  rationale?: string;      // distilled "why" (from the summarizer)
  content: string;
  citations?: Citation[];
  tool?: string;           // e.g. "knowledge_search"
}

export const MOCK_THREAD: AgentTurn[] = [
  { author: "You", role: "user", color: "text-slate-700", content: "I want a refund for order #4821 — it was $640 and I never received it." },
  {
    author: "Router", role: "agent", color: "text-indigo-600",
    rationale: "Classified the request as a refund with a high dollar amount; routed to the Refund Specialist and flagged for a fraud check.",
    content: "This is a refund request for $640 (high-value). Handing off to the Refund Specialist; Fraud Checker should verify first.",
  },
  {
    author: "Fraud Checker", role: "agent", color: "text-rose-600",
    tool: "knowledge_search",
    rationale: "Checked the order against risk signals; no anomalies, but amount exceeds the dual-approval threshold noted in policy.",
    content: "No fraud signals on order #4821. Note: amount exceeds the $500 threshold, so secondary approval is required.",
    citations: [{ source: "security-policy.pdf", kb: "Company Policies" }],
  },
  {
    author: "Refund Specialist", role: "agent", color: "text-emerald-600",
    tool: "knowledge_search",
    rationale: "Confirmed the refund is eligible (within 30 days, item not received) and that >$500 needs a team-lead approval before processing.",
    content: "Order #4821 is eligible for a full refund. Because it's over $500, I've queued it for team-lead approval, then it'll be refunded to your original payment method in 5–7 business days.",
    citations: [
      { source: "refund-policy.pdf", kb: "Company Policies" },
    ],
  },
];

// ── Conversations list + memory ─────────────────────────────────────────────
export interface Conversation {
  id: string;
  title: string;
  env: "sandbox" | "production";
  agent: string;
  updatedAt: string;
  preview: string;
  turns: number;
}
export const MOCK_CONVERSATIONS: Conversation[] = [
  { id: "c1", title: "Refund for order #4821", env: "production", agent: "refund-assistant", updatedAt: "2026-07-14T09:20:00Z", preview: "Order #4821 is eligible for a full refund…", turns: 6 },
  { id: "c2", title: "Is my data encrypted at rest?", env: "production", agent: "policy-qa", updatedAt: "2026-07-13T16:02:00Z", preview: "Per the security policy, all transactions over $10k…", turns: 3 },
  { id: "c3", title: "Testing travel reimbursement flow", env: "sandbox", agent: "policy-qa", updatedAt: "2026-07-13T11:40:00Z", preview: "Travel over $2k requires manager sign-off…", turns: 4 },
  { id: "c4", title: "Draft onboarding checklist", env: "sandbox", agent: "refund-assistant", updatedAt: "2026-07-12T09:15:00Z", preview: "Here is a first pass at the checklist…", turns: 8 },
];

export interface MemoryRow {
  role: "user" | "assistant";
  author: string;
  content: string;
  at: string;
}
export const MOCK_MEMORY: MemoryRow[] = [
  { role: "user", author: "You", content: "I want a refund for order #4821 — it was $640 and I never received it.", at: "09:18" },
  { role: "assistant", author: "refund-assistant", content: "Order #4821 is eligible for a full refund. Because it's over $500, I've queued it for team-lead approval…", at: "09:19" },
  { role: "user", author: "You", content: "How long will the approval take?", at: "09:20" },
  { role: "assistant", author: "refund-assistant", content: "Team-lead approvals are usually completed within one business day; you'll get an email when it's processed.", at: "09:20" },
];

// ── User profile presets ────────────────────────────────────────────────────
export const PREFERENCE_OPTIONS = {
  length: ["concise", "balanced", "detailed"],
  tone: ["professional", "neutral", "casual"],
  format: ["prose", "bulleted", "structured"],
  language: ["English", "Spanish", "French", "German", "Japanese"],
  expertise: ["beginner", "intermediate", "expert"],
} as const;

export const DEFAULT_PREFERENCES = {
  length: "concise",
  tone: "professional",
  format: "bulleted",
  language: "English",
  expertise: "intermediate",
};
