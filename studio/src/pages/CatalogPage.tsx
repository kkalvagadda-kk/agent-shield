import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, RefreshCw, Rocket, ShieldOff } from "lucide-react";
import { Link } from "react-router-dom";
import { listCatalog, CatalogArtifact } from "../api/catalogApi";

const TYPE_COLORS: Record<string, string> = {
  agent:    "bg-blue-50 text-blue-700 border border-blue-200",
  tool:     "bg-purple-50 text-purple-700 border border-purple-200",
  skill:    "bg-teal-50 text-teal-700 border border-teal-200",
  workflow: "bg-indigo-50 text-indigo-700 border border-indigo-200",
};

const ACTIVE_TYPE_COLORS: Record<string, string> = {
  agent:    "bg-blue-600 text-white border border-blue-600",
  tool:     "bg-purple-600 text-white border border-purple-600",
  skill:    "bg-teal-600 text-white border border-teal-600",
  workflow: "bg-indigo-600 text-white border border-indigo-600",
};

const ASSET_TYPES = ["agent", "tool", "skill", "workflow"] as const;
type AssetType = (typeof ASSET_TYPES)[number];

export default function CatalogPage() {
  const [typeFilter, setTypeFilter] = useState<AssetType | null>(null);

  const { data: artifacts = [], isLoading, refetch, isFetching } = useQuery({
    queryKey: ["catalog", typeFilter],
    queryFn: () => listCatalog(typeFilter ? { type: typeFilter } : undefined),
  });

  const { data: allArtifacts = [] } = useQuery({
    queryKey: ["catalog"],
    queryFn: () => listCatalog(),
  });

  return (
    <div className="max-w-5xl mx-auto px-6 py-8">
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Catalog</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Published artifacts available for production deployment
          </p>
        </div>
        <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
          <RefreshCw size={13} className={isFetching ? "animate-spin" : ""} /> Refresh
        </button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" /> Loading catalog…
        </div>
      )}

      {!isLoading && allArtifacts.length === 0 && (
        <div className="card flex flex-col items-center py-16 text-center">
          <ShieldOff size={36} className="text-slate-300 mb-3" />
          <p className="text-slate-500 font-medium">No published artifacts yet</p>
          <p className="text-slate-400 text-sm mt-1">
            Publish agents or workflows from the playground, then approve via Administration.
          </p>
        </div>
      )}

      {!isLoading && allArtifacts.length > 0 && (
        <div className="space-y-3">
          <TypeFilters
            artifacts={allArtifacts}
            activeType={typeFilter}
            onToggle={(t) => setTypeFilter(typeFilter === t ? null : t)}
          />
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {artifacts.map((a) => (
              <CatalogCard key={a.id} artifact={a} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function TypeFilters({
  artifacts,
  activeType,
  onToggle,
}: {
  artifacts: CatalogArtifact[];
  activeType: AssetType | null;
  onToggle: (t: AssetType) => void;
}) {
  const counts = artifacts.reduce<Record<string, number>>((acc, a) => {
    acc[a.type] = (acc[a.type] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex gap-2 flex-wrap mb-2">
      {ASSET_TYPES.filter((t) => counts[t]).map((t) => (
        <button
          key={t}
          onClick={() => onToggle(t)}
          className={`badge cursor-pointer transition-colors ${
            activeType === t ? ACTIVE_TYPE_COLORS[t] : TYPE_COLORS[t]
          }`}
        >
          {t}s <span className="ml-1 font-normal opacity-70">{counts[t]}</span>
        </button>
      ))}
      {activeType && (
        <button
          onClick={() => onToggle(activeType)}
          className="text-xs text-slate-400 hover:text-slate-600 underline"
        >
          Clear filter
        </button>
      )}
    </div>
  );
}

function CatalogCard({ artifact }: { artifact: CatalogArtifact }) {
  return (
    <Link
      to={`/catalog/${artifact.id}`}
      className="card flex flex-col gap-3 hover:border-blue-300 hover:shadow-md transition-all cursor-pointer"
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="font-semibold text-slate-900 text-sm leading-snug">{artifact.name}</h3>
        <span className={`badge shrink-0 ${TYPE_COLORS[artifact.type]}`}>{artifact.type}</span>
      </div>

      {artifact.description && (
        <p className="text-xs text-slate-500 line-clamp-2">{artifact.description}</p>
      )}

      <div className="mt-auto pt-2 border-t border-slate-100 flex items-center justify-between">
        <p className="text-xs text-slate-400">
          Owner: <span className="font-medium text-slate-600">{artifact.team}</span>
        </p>
        <div className="flex items-center gap-2">
          {artifact.latest_version && (
            <span className="badge bg-green-50 text-green-700 border border-green-200 text-xs">
              {artifact.latest_version}
            </span>
          )}
          {artifact.deployment_count > 0 && (
            <span className="flex items-center gap-1 text-xs text-slate-500">
              <Rocket size={11} /> {artifact.deployment_count}
            </span>
          )}
        </div>
      </div>
    </Link>
  );
}
