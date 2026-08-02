// Ten runs at a glance.
//
// A single "last run" answers the wrong question. It cannot tell FLAKY from BROKEN
// from FINE — a schedule that failed once at 3am and has been green since looks
// identical to one that has never succeeded. That distinction is the first thing an
// operator wants and the reason this column exists.
//
// Statuses come from the SAME query as the rest of the row (`recent_runs`, a lateral
// on agent_runs keyed by trigger_id). Fetching per row would be N+1 against an
// endpoint that already has the joins.

const BAR: Record<string, { cls: string; label: string }> = {
  completed: { cls: "bg-green-500", label: "succeeded" },
  running: { cls: "bg-blue-400 animate-pulse", label: "running" },
  queued: { cls: "bg-slate-300", label: "queued" },
  failed: { cls: "bg-red-500", label: "failed" },
  cancelled: { cls: "bg-slate-400", label: "cancelled" },
  parked: { cls: "bg-amber-400", label: "awaiting approval" },
};

export default function RunSparkline({ statuses }: { statuses: string[] }) {
  if (statuses.length === 0) {
    // Not the same as "all failed", and must not look like it. A schedule that has
    // never fired is a normal state for a newly armed one.
    return (
      <span className="text-xs text-slate-300" data-testid="run-sparkline-empty">
        no runs yet
      </span>
    );
  }

  // Server sends newest first; render oldest → newest so the eye reads left-to-right
  // as time, which is what every other timeline in the product does.
  const ordered = [...statuses].reverse();
  const failed = statuses.filter((s) => s === "failed").length;

  return (
    <span
      className="inline-flex items-end gap-[2px]"
      data-testid="run-sparkline"
      data-failed={failed}
      data-count={statuses.length}
      // One title for the whole strip: per-bar tooltips on a 10px target are not
      // reachable with a trackpad, let alone a keyboard.
      title={
        `last ${statuses.length} run${statuses.length === 1 ? "" : "s"}, oldest first: ` +
        ordered.map((s) => BAR[s]?.label ?? s).join(", ")
      }
    >
      {ordered.map((s, i) => (
        <span
          key={i}
          className={`w-[5px] h-4 rounded-sm ${BAR[s]?.cls ?? "bg-slate-200"}`}
        />
      ))}
    </span>
  );
}
