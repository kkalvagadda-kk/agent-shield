import { Layers } from "lucide-react";

// Communicates that a surface is not a standalone page — it renders *inside*
// several existing consoles. `active` marks the one currently being previewed.
export default function ConsoleContextBar({
  consoles,
  active,
  note,
}: {
  consoles: string[];
  active: string;
  note: string;
}) {
  return (
    <div className="rounded-lg bg-slate-100 border border-slate-200 px-3 py-2 mb-5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-500 uppercase tracking-wider">
          <Layers size={13} /> Appears in
        </span>
        {consoles.map((c) => (
          <span
            key={c}
            className={`px-2 py-0.5 rounded-md text-xs ${c === active ? "bg-blue-600 text-white font-medium" : "bg-white text-slate-500 border border-slate-200"}`}
          >
            {c}
          </span>
        ))}
      </div>
      <p className="text-xs text-slate-500 mt-1.5">{note}</p>
    </div>
  );
}
