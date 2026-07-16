// ---------------------------------------------------------------------------
// AttributedBubble.tsx — presentational chat bubble with optional per-agent
// attribution.
//
// One component backs all three chat surfaces (AgentChatPage, ChatPane,
// CatalogChatPage). Single-agent is the degenerate case: pass no `author` (or
// `showLabel={false}`) and it renders an unlabeled bubble exactly like today. A
// multi-agent workflow passes each member's name as `author`, and the bubble
// shows a colored dot + the agent name so the run reads as a real multi-speaker
// conversation. Color comes from `agentColor` (deterministic per name).
//
// Purely presentational: no data fetching, no stream logic. Chips / feedback /
// safety details go in the `children` slot so surfaces keep their existing UI.
// ---------------------------------------------------------------------------

import { type ReactNode } from "react";
import { agentColor } from "../../lib/agentColor";

export interface AttributedBubbleProps {
  role: string;
  content: string;
  author?: string; // agent name; undefined → unlabeled
  showLabel?: boolean; // default true; label renders only when author is also set
  streaming?: boolean; // show a blinking caret
  children?: ReactNode; // chips / feedback / safety details slot
}

export default function AttributedBubble({
  role,
  content,
  author,
  showLabel = true,
  streaming = false,
  children,
}: AttributedBubbleProps) {
  const isUser = role === "user";
  // Label + color dot only when there is an author AND the caller allows it
  // (the caller knows whether the surface has more than one speaker).
  const labelled = !isUser && !!author && showLabel !== false;
  const color = agentColor(author);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="max-w-[70%]">
        {labelled && (
          <div className="mb-0.5 flex items-center gap-1.5 px-1">
            <span className={`inline-block w-2 h-2 rounded-full ${color.dot}`} />
            <span className={`text-xs font-medium ${color.text}`}>{author}</span>
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
      </div>
    </div>
  );
}
