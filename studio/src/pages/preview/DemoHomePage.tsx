import { Link } from "react-router-dom";
import { Database, MessagesSquare, SlidersHorizontal, History, ArrowRight } from "lucide-react";

const CARDS = [
  {
    to: "/knowledge",
    icon: Database,
    title: "Knowledge Base",
    desc: "Create a Base, add Sources with ingestion status, inspect chunks, and test retrieval before attaching to an agent.",
    tag: "RAG",
    where: "Own destination — Build › Knowledge",
  },
  {
    to: "/preview/chat",
    icon: MessagesSquare,
    title: "Multi-agent conversation",
    desc: "A shared workflow thread where each agent's turn is attributed, with rationale and Knowledge Base citations.",
    tag: "Context sharing",
    where: "Renders inside: Playground · Consumer chat · Workflow Builder · Eval Results",
  },
  {
    to: "/preview/conversations",
    icon: History,
    title: "Conversations & memory",
    desc: "List past conversations across sandbox and production, continue one, and view what's stored in memory.",
    tag: "Sessions",
    where: "Docks into: Playground · Deployed chat · Consumer chat · Deployment Overview",
  },
  {
    to: "/preferences",
    icon: SlidersHorizontal,
    title: "Response preferences",
    desc: "Structured presets (length, tone, format, language, expertise) that shape how agents respond to you.",
    tag: "User profile",
    where: "Account menu (sidebar footer) — user-global, one place",
  },
];

export default function DemoHomePage() {
  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <div className="mb-8">
        <span className="badge bg-blue-100 text-blue-700 mb-3 inline-block">UX Preview · mock data</span>
        <h1 className="text-3xl font-bold text-slate-900">Context Storage & Knowledge Base</h1>
        <p className="text-slate-500 mt-2 max-w-2xl">
          A clickable prototype of the context-management UX from{" "}
          <code className="font-mono bg-slate-100 px-1 rounded text-xs">docs/design/context-storage-architecture.md</code>.
          Everything here is stubbed with sample data — no backend. Navigate, and tell me what feels right or wrong.
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {CARDS.map((c) => (
          <Link
            key={c.to}
            to={c.to}
            className="card group hover:border-blue-300 hover:shadow-md transition-all flex flex-col"
          >
            <div className="flex items-start justify-between">
              <div className="w-10 h-10 rounded-lg bg-blue-500/10 flex items-center justify-center text-blue-600">
                <c.icon size={20} />
              </div>
              <span className="badge bg-slate-100 text-slate-500">{c.tag}</span>
            </div>
            <h2 className="text-lg font-semibold text-slate-900 mt-4">{c.title}</h2>
            <p className="text-sm text-slate-500 mt-1 flex-1">{c.desc}</p>
            <p className="text-xs text-slate-400 mt-3 pt-3 border-t border-slate-100">{c.where}</p>
            <span className="text-sm text-blue-600 font-medium mt-3 inline-flex items-center gap-1 group-hover:gap-2 transition-all">
              Open <ArrowRight size={14} />
            </span>
          </Link>
        ))}
      </div>

      <p className="text-xs text-slate-400 mt-8">
        The rest of the sidebar is the real Studio shell (empty without a backend). The four screens above are the new work.
      </p>
    </div>
  );
}
