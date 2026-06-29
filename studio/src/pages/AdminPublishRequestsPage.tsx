import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Loader2, RefreshCw, XCircle } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import {
  approvePublishRequest,
  listPublishRequests,
  rejectPublishRequest,
  type PublishRequest,
} from "../api/registryApi";

const STATUS_CHIP: Record<string, string> = {
  pending_review: "bg-amber-100 text-amber-700",
  approved:       "bg-green-100 text-green-700",
  rejected:       "bg-red-100 text-red-700",
};

const RISK_CHIP: Record<string, string> = {
  low:    "bg-blue-50 text-blue-600",
  medium: "bg-amber-50 text-amber-700",
  high:   "bg-red-100 text-red-700",
};

export default function AdminPublishRequestsPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<string>("pending_review");
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [teamsInput, setTeamsInput] = useState("");
  const [rejectNotes, setRejectNotes] = useState("");

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["publish-requests", statusFilter],
    queryFn: () => listPublishRequests({ status: statusFilter || undefined, limit: 100 }),
  });

  const approveMutation = useMutation({
    mutationFn: ({ id, teams }: { id: string; teams: string[] }) =>
      approvePublishRequest(id, { grantee_teams: teams }),
    onSuccess: (result) => {
      toast.success(`Approved — ${result.grants_created} grant(s) created.`);
      setApprovingId(null);
      setTeamsInput("");
      qc.invalidateQueries({ queryKey: ["publish-requests"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Approval failed.");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, notes }: { id: string; notes: string }) =>
      rejectPublishRequest(id, notes),
    onSuccess: () => {
      toast.success("Publish request rejected.");
      setRejectingId(null);
      setRejectNotes("");
      qc.invalidateQueries({ queryKey: ["publish-requests"] });
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })
        ?.response?.data?.detail;
      toast.error(msg ?? "Rejection failed.");
    },
  });

  const handleApprove = (pr: PublishRequest) => {
    const teams = teamsInput.split(",").map((t) => t.trim()).filter(Boolean);
    if (teams.length === 0) {
      toast.error("Enter at least one grantee team.");
      return;
    }
    approveMutation.mutate({ id: pr.id, teams });
  };

  const handleReject = (pr: PublishRequest) => {
    rejectMutation.mutate({ id: pr.id, notes: rejectNotes });
  };

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Publish Requests</h1>
          <p className="text-sm text-slate-500 mt-0.5">Review and approve asset publish requests</p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input text-sm w-44"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="pending_review">Pending Review</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
          <button onClick={() => refetch()} disabled={isFetching} className="btn-secondary">
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading requests…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load publish requests: {String(error)}
        </div>
      )}

      {data && (
        <div className="card p-0 overflow-hidden">
          {data.items.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <CheckCircle size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No publish requests</p>
              <p className="text-slate-400 text-sm mt-1">Nothing in this queue right now.</p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Asset Type", "Asset ID", "Submitted By", "Submitted At", "Status", "Risk", "Actions"].map(
                    (h) => (
                      <th
                        key={h}
                        className="px-4 py-3 text-left text-xs font-semibold text-slate-500 uppercase tracking-wider"
                      >
                        {h}
                      </th>
                    )
                  )}
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {data.items.map((pr) => (
                  <>
                    <tr key={pr.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3">
                        <span className="badge bg-slate-100 text-slate-600">{pr.asset_type}</span>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-500">
                        {pr.asset_id.slice(0, 8)}…
                      </td>
                      <td className="px-4 py-3 text-slate-700">{pr.submitted_by}</td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {new Date(pr.submitted_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${STATUS_CHIP[pr.status] ?? "bg-slate-100 text-slate-600"}`}>
                          {pr.status.replace("_", " ")}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`badge ${RISK_CHIP[pr.highest_risk_level] ?? "bg-slate-100 text-slate-600"}`}>
                          {pr.highest_risk_level}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {pr.status === "pending_review" && (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => {
                                setApprovingId(approvingId === pr.id ? null : pr.id);
                                setRejectingId(null);
                              }}
                              className="inline-flex items-center gap-1 text-xs text-green-600 hover:text-green-800 font-medium"
                            >
                              <CheckCircle size={12} />
                              Approve
                            </button>
                            <button
                              onClick={() => {
                                setRejectingId(rejectingId === pr.id ? null : pr.id);
                                setApprovingId(null);
                              }}
                              className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 font-medium"
                            >
                              <XCircle size={12} />
                              Reject
                            </button>
                          </div>
                        )}
                        {pr.status !== "pending_review" && pr.reviewed_by && (
                          <span className="text-xs text-slate-400">by {pr.reviewed_by}</span>
                        )}
                      </td>
                    </tr>

                    {/* Inline Approve form */}
                    {approvingId === pr.id && (
                      <tr key={`approve-${pr.id}`} className="bg-green-50 border-b border-green-100">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="flex items-end gap-3">
                            <div className="flex-1">
                              <label className="label text-xs mb-1">
                                Grantee teams (comma-separated)
                              </label>
                              <input
                                className="input text-sm"
                                placeholder="platform, operations"
                                value={teamsInput}
                                onChange={(e) => setTeamsInput(e.target.value)}
                              />
                            </div>
                            <button
                              onClick={() => handleApprove(pr)}
                              disabled={approveMutation.isPending}
                              className="btn-primary text-xs py-2"
                            >
                              {approveMutation.isPending ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                "Confirm Approve"
                              )}
                            </button>
                            <button
                              onClick={() => setApprovingId(null)}
                              className="btn-secondary text-xs py-2"
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}

                    {/* Inline Reject form */}
                    {rejectingId === pr.id && (
                      <tr key={`reject-${pr.id}`} className="bg-red-50 border-b border-red-100">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="flex items-end gap-3">
                            <div className="flex-1">
                              <label className="label text-xs mb-1">Rejection notes (optional)</label>
                              <input
                                className="input text-sm"
                                placeholder="Reason for rejection…"
                                value={rejectNotes}
                                onChange={(e) => setRejectNotes(e.target.value)}
                              />
                            </div>
                            <button
                              onClick={() => handleReject(pr)}
                              disabled={rejectMutation.isPending}
                              className="btn-primary bg-red-600 hover:bg-red-700 text-xs py-2"
                            >
                              {rejectMutation.isPending ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                "Confirm Reject"
                              )}
                            </button>
                            <button
                              onClick={() => setRejectingId(null)}
                              className="btn-secondary text-xs py-2"
                            >
                              Cancel
                            </button>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {data && (
        <p className="text-xs text-slate-400 mt-2 text-right">
          {data.total} total request{data.total !== 1 ? "s" : ""}
        </p>
      )}
    </div>
  );
}
