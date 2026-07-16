// ---------------------------------------------------------------------------
// ToolCallChip.tsx — a compact "called <tool>" chip shown under an agent's
// bubble when that member invoked a platform tool (POC-2b, 2b-i).
//
// Purely presentational: the tool name + a Database glyph. An error status
// tints the chip red so a failed tool call reads differently from a successful
// one. Matches the Multi-Agent Chat mock (MultiAgentChatPage lines 56–60).
// ---------------------------------------------------------------------------

import { Database } from "lucide-react";

export default function ToolCallChip({ tool, status }: { tool: string; status?: string }) {
  const isError = status === "error";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-md bg-slate-100 px-2 py-1 text-xs ${
        isError ? "text-red-600" : "text-slate-500"
      }`}
    >
      <Database size={11} /> called <code className="font-mono">{tool}</code>
    </span>
  );
}
