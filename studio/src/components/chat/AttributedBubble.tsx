// ---------------------------------------------------------------------------
// AttributedBubble.tsx — presentational chat bubble with optional per-agent
// attribution + rich slots (avatar / tool chips / rationale / citations).
//
// One component backs all three chat surfaces (AgentChatPage, ChatPane,
// CatalogChatPage). Single-agent is the degenerate case: pass no `author` (or
// `showLabel={false}`) and no rich props and it renders an unlabeled bubble
// exactly like today (DOM byte-identical). A multi-agent workflow passes each
// member's name as `author`, and the bubble shows a colored dot + the agent
// name so the run reads as a real multi-speaker conversation. Color comes from
// `agentColor` (deterministic per name).
//
// POC-2b rich slots (all opt-in, so degenerate render is unchanged):
//   - `avatar`    → a tinted Bot icon in the name header.
//   - `toolCalls` → a ToolCallChip row ABOVE the content box.
//   - `rationale` → an amber "why" box ABOVE the content box (gated by
//                   `showRationale`, default true).
//   - `citations` → a chip row BELOW the content box (empty in POC-2b; the
//                   slot exists so the renderer is ready for POC-4).
//
// Purely presentational: no data fetching, no stream logic. Chips / feedback /
// safety details go in the `children` slot so surfaces keep their existing UI.
// ---------------------------------------------------------------------------

import { type ReactNode } from "react";
import { Bot, Lightbulb, Database } from "lucide-react";
import { agentColor } from "../../lib/agentColor";
import ToolCallChip from "./ToolCallChip";

export interface AttributedBubbleProps {
  role: string;
  content: string;
  author?: string; // agent name; undefined → unlabeled
  showLabel?: boolean; // default true; label renders only when author is also set
  streaming?: boolean; // show a blinking caret
  children?: ReactNode; // chips / feedback / safety details slot
  // --- POC-2b rich slots (opt-in) ---
  avatar?: boolean; // render a tinted Bot avatar in the name header
  toolCalls?: { tool_name: string; status: string }[]; // ToolCallChip row above content
  rationale?: string | null; // amber "why" box above content
  showRationale?: boolean; // default true; gates the amber box
  citations?: { source: string; kb: string }[]; // empty in POC-2b (slot only)
}

export default function AttributedBubble({
  role,
  content,
  author,
  showLabel = true,
  streaming = false,
  children,
  avatar = false,
  toolCalls,
  rationale,
  showRationale = true,
  citations,
}: AttributedBubbleProps) {
  const isUser = role === "user";
  // Label + color dot only when there is an author AND the caller allows it
  // (the caller knows whether the surface has more than one speaker).
  const labelled = !isUser && !!author && showLabel !== false;
  const color = agentColor(author);

  const hasTools = !isUser && !!toolCalls && toolCalls.length > 0;
  const hasRationale = !isUser && showRationale !== false && !!rationale;
  const hasCitations = !isUser && !!citations && citations.length > 0;

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="max-w-[70%]">
        {labelled && (
          <div className="mb-0.5 flex items-center gap-1.5 px-1">
            {avatar && <Bot size={13} className={color.text} />}
            <span className={`inline-block w-2 h-2 rounded-full ${color.dot}`} />
            <span className={`text-xs font-medium ${color.text}`}>{author}</span>
          </div>
        )}
        {hasTools && (
          <div className="mb-1.5 flex flex-wrap gap-1.5">
            {toolCalls!.map((tc, i) => (
              <ToolCallChip key={i} tool={tc.tool_name} status={tc.status} />
            ))}
          </div>
        )}
        {hasRationale && (
          <div className="mb-1.5 flex max-w-lg items-start gap-1.5 rounded-md border border-amber-100 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-700">
            <Lightbulb size={12} className="mt-0.5 shrink-0" />
            <span>
              <span className="font-semibold">Rationale:</span> {rationale}
            </span>
          </div>
        )}
        <div
          className={`px-4 py-2.5 rounded-2xl text-sm whitespace-pre-wrap ${
            isUser
              ? "bg-blue-600 text-white rounded-br-sm"
              : "bg-slate-100 text-slate-800 rounded-bl-sm"
          }`}
        >
          {content}
          {streaming && !isUser && (
            <span className="inline-block w-1 h-3 bg-slate-400 ml-0.5 animate-pulse" />
          )}
          {children}
        </div>
        {hasCitations && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {citations!.map((c, i) => (
              <span
                key={i}
                className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-0.5 text-xs text-slate-600"
              >
                <Database size={10} className="text-blue-500" /> {c.source}{" "}
                <span className="text-slate-400">· {c.kb}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
