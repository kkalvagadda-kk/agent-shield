import { ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";

export interface SafetyResult {
  reason: string;
  type: string;
  scanners?: { name: string; risk_score?: number; blocked: boolean; reason?: string }[];
}

export default function SafetyDetails({ result }: { result: SafetyResult }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mt-2 border border-orange-200 bg-orange-50 rounded-lg overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-orange-800 hover:bg-orange-100 transition-colors"
      >
        <ShieldAlert size={14} className="text-orange-600 shrink-0" />
        <span className="font-medium">Blocked by safety scan</span>
        <span className="text-xs text-orange-600 ml-auto">{expanded ? "▾" : "▸"}</span>
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-orange-200">
          <p className="text-xs text-orange-700 pt-2">{result.reason}</p>

          {result.scanners && result.scanners.length > 0 && (
            <div className="space-y-1.5">
              {result.scanners.map((s, i) => (
                <div key={i} className="flex items-center gap-2 text-xs">
                  {s.blocked ? (
                    <ShieldAlert size={12} className="text-red-500 shrink-0" />
                  ) : (
                    <ShieldCheck size={12} className="text-green-500 shrink-0" />
                  )}
                  <span className="font-medium text-slate-700">{s.name}</span>
                  {s.risk_score != null && (
                    <div className="flex items-center gap-1">
                      <div className="w-16 h-1.5 bg-slate-200 rounded overflow-hidden">
                        <div
                          className={`h-full rounded ${s.risk_score > 0.7 ? "bg-red-400" : s.risk_score > 0.4 ? "bg-yellow-400" : "bg-green-400"}`}
                          style={{ width: `${s.risk_score * 100}%` }}
                        />
                      </div>
                      <span className="text-slate-500">{(s.risk_score * 100).toFixed(0)}%</span>
                    </div>
                  )}
                  {s.reason && <span className="text-slate-500">{s.reason}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
