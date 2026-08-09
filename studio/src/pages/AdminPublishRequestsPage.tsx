import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Eye, Loader2, RefreshCw, XCircle, FlaskConical } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { scoreColor, thresholdLabel } from "../lib/evalVerdict";
import PublishReviewDrawer from "../components/admin/PublishReviewDrawer";
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
  const navigate = useNavigate();
  const [statusFilter, setStatusFilter] = useState<string>("pending_review");
  // The row no longer approves. Decision 47 step D option B: Approve moved INTO the
  // review drawer, so "the reviewer was shown the tools, the endpoints, the credentials
  // and the cascade" is structurally true rather than hoped-for. Leaving an Approve on
  // the row would have made the drawer optional, which is today's behaviour for anyone
  // in a hurry — and today's behaviour is the gap (G-R3-11).
  const [reviewingId, setReviewingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [rejectNotes, setRejectNotes] = useState("");

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["publish-requests", statusFilter],
    queryFn: () => listPublishRequests({ status: statusFilter || undefined, limit: 100 }),
  });


  const approveMutation = useMutation({
    mutationFn: ({ id }: { id: string }) =>
      approvePublishRequest(id),
    onSuccess: () => {
      toast.success("Promoted to catalog. Go to Access Control to grant team access.");
      setReviewingId(null);
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
                  {["Asset Type", "Asset", "Submitted By", "Submitted At", "Last Eval", "Status", "Risk", "Actions"].map(
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
                      <td className="px-4 py-3">
                        <p className="text-sm font-medium text-slate-800">
                          {pr.asset_name ?? `${pr.asset_id.slice(0, 8)}…`}
                        </p>
                        {pr.asset_team && (
                          <p className="text-xs text-slate-400">
                            Team: <span className="font-medium">{pr.asset_team}</span>
                          </p>
                        )}
                      </td>
                      <td className="px-4 py-3 text-slate-700">{pr.submitted_by}</td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {new Date(pr.submitted_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        {/* The verdict a human approves a release on. Two things were
                            wrong here and both are fixed by the SERVER, not by this
                            markup: the score could belong to a DIFFERENT VERSION than
                            the one being published (the eval was resolved by agent
                            name alone), and it was graded against a hardcoded 0.7
                            regardless of the threshold that run actually used.
                            `eval_source` now says where the number came from, and
                            `last_eval_pass_threshold` is the bar it had to clear.
                            Decision 32. */}
                        {pr.last_eval_score != null ? (
                          <div className="flex flex-col gap-0.5">
                            <button
                              onClick={() => pr.last_eval_run_id && navigate(`/playground/eval-runs/${pr.last_eval_run_id}`)}
                              className={`badge text-xs cursor-pointer ${scoreColor(
                                pr.last_eval_score,
                                pr.last_eval_pass_threshold,
                              )}`}
                              title={thresholdLabel(pr.last_eval_score, pr.last_eval_pass_threshold)}
                            >
                              <FlaskConical size={10} className="mr-0.5 inline" />
                              {Math.round(pr.last_eval_score * 100)}%
                            </button>
                            {/* Why a good-looking score will not publish. */}
                            <span className="text-[10px] text-slate-400" data-testid="eval-threshold-label">
                              {thresholdLabel(pr.last_eval_score, pr.last_eval_pass_threshold)}
                            </span>
                            {pr.eval_source === "agent_latest" && (
                              /* The score is real but it is not about THIS version.
                                 Silently rendering it as if it were is the bug. */
                              <span
                                className="badge bg-amber-100 text-amber-700 text-[10px]"
                                data-testid="eval-provenance-warning"
                                title="This request pins no version, so the score shown is the agent's most recent eval — not an evaluation of what is being published."
                              >
                                from a different version
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="badge bg-amber-50 text-amber-600 text-xs">No eval</span>
                        )}
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
                                setReviewingId(pr.id);
                                setRejectingId(null);
                              }}
                              className="inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 font-medium"
                              data-testid={`review-open-${pr.id}`}
                            >
                              <Eye size={12} />
                              Review &amp; Promote
                            </button>
                            <button
                              onClick={() => {
                                setRejectingId(rejectingId === pr.id ? null : pr.id);
                                setReviewingId(null);
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

      {reviewingId && (
        <PublishReviewDrawer
          requestId={reviewingId}
          onClose={() => setReviewingId(null)}
          onApprove={() => approveMutation.mutate({ id: reviewingId })}
          approving={approveMutation.isPending}
        />
      )}

      {data && (
        <p className="text-xs text-slate-400 mt-2 text-right">
          {data.total} total request{data.total !== 1 ? "s" : ""}
        </p>
      )}
    </div>
  );
}
