/**
 * One headline number with a label and optional sub-caption.
 *
 * Lifted verbatim from CostConsolePage, which had the cleanest version, so the eval
 * surfaces (Slice 0's threshold tiles, Wave 1's trend/diff headers) render the same
 * shape rather than growing a third variant. Presentation only — no formatting, no
 * verdict logic. Verdict colour comes from `lib/evalVerdict`.
 */
export function StatCard({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="bg-white border border-slate-200 rounded-lg p-4">
      <p className="text-xs text-slate-500 uppercase tracking-wide">{label}</p>
      <p className="text-2xl font-semibold text-slate-800 mt-1">{value}</p>
      {sub && <p className="text-xs text-slate-400 mt-0.5">{sub}</p>}
    </div>
  );
}

export default StatCard;
