import { Terminal } from "lucide-react";

export interface TraceEvent {
  ts: string;
  event: string;
  content?: string;
  tool_name?: string;
  result?: string;
}

interface Props {
  events: TraceEvent[];
  collapsed: boolean;
  onToggle: () => void;
}

const EVENT_COLOR: Record<string, string> = {
  text_delta:      "text-slate-600",
  tool_call_start: "text-blue-600",
  tool_call_end:   "text-green-600",
  approval_requested: "text-amber-600",
  done:            "text-slate-400",
};

export default function TracePanel({ events, collapsed, onToggle }: Props) {
  return (
    <div
      className={`flex flex-col border-l border-slate-200 bg-slate-50 transition-all ${
        collapsed ? "w-8" : "w-72"
      }`}
    >
      <button
        onClick={onToggle}
        className="flex items-center gap-2 px-2 py-2 text-xs text-slate-500 hover:text-slate-700 border-b border-slate-200"
      >
        <Terminal size={14} />
        {!collapsed && <span className="font-medium">Event Trace</span>}
      </button>

      {!collapsed && (
        <div className="flex-1 overflow-auto p-2 space-y-1">
          {events.length === 0 ? (
            <p className="text-xs text-slate-400 italic">No events yet.</p>
          ) : (
            events.map((ev, i) => (
              <div key={i} className={`text-xs font-mono ${EVENT_COLOR[ev.event] ?? "text-slate-500"}`}>
                <span className="text-slate-300 mr-1">{ev.ts.slice(11, 19)}</span>
                <span className="font-semibold">[{ev.event}]</span>
                {ev.tool_name && <span className="ml-1">{ev.tool_name}</span>}
                {ev.content && (
                  <span className="ml-1 text-slate-500">
                    {ev.content.length > 60 ? ev.content.slice(0, 60) + "…" : ev.content}
                  </span>
                )}
                {ev.result && (
                  <span className="ml-1 text-green-500">→ {ev.result.slice(0, 40)}</span>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
