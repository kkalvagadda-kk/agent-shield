import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle, Loader2, RefreshCw, XCircle } from "lucide-react";
import { toast } from "sonner";
import axios from "axios";

const http = axios.create({ baseURL: "/api/v1" });

interface Approval {
  id: string;
  agent_name: string;
  tool_name: string;
  risk_level: string;
  status: string;
  context: string;
  created_at: string;
  expires_at: string;
  reviewer_id: string | null;
  reviewer_notes: string | null;
  tool_args: Record<string, unknown>;
  version: number;
}

const RISK_CHIP: Record<string, string> = {
  high:     "bg-amber-100 text-amber-700",
  critical: "bg-red-100 text-red-700",
};

const CONTEXT_CHIP: Record<string, string> = {
  production: "bg-blue-100 text-blue-700",
};

async function fetchApprovals(statusFilter?: string): Promise<{ items: Approval[]; total: number }> {
  const params: Record<string, string> = {};
  if (statusFilter) params.status = statusFilter;
  const { data } = await http.get<{ items: Approval[]; total: number }>("/approvals/", { params });
  return data;
}

async function decideApproval(
  id: string,
  decision: "approved" | "rejected",
  version: number,
  reviewer_notes?: string
): Promise<Approval> {
  const { data } = await http.patch<Approval>(`/approvals/${id}`, {
    decision,
    reviewer_id: "studio-user",
    reviewer_notes: reviewer_notes ?? null,
    version,
  });
  return data;
}

export default function HITLDashboardPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState("pending");
  const [approvingId, setApprovingId] = useState<string | null>(null);
  const [rejectingId, setRejectingId] = useState<string | null>(null);
  const [reviewerNotes, setReviewerNotes] = useState("");
  const autoRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["hitl-approvals", statusFilter],
    queryFn: () => fetchApprovals(statusFilter || undefined),
    refetchInterval: 30_000,
  });

  // Clear auto-refresh on unmount
  useEffect(() => {
    return () => {
      if (autoRefreshRef.current) clearInterval(autoRefreshRef.current);
    };
  }, []);

  const approveMutation = useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      decideApproval(id, "approved", version, reviewerNotes),
    onSuccess: () => {
      toast.success("Approval granted.");
      setApprovingId(null);
      setReviewerNotes("");
      qc.invalidateQueries({ queryKey: ["hitl-approvals"] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Approval failed.");
    },
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, version }: { id: string; version: number }) =>
      decideApproval(id, "rejected", version),
    onSuccess: () => {
      toast.success("Approval rejected.");
      setRejectingId(null);
      qc.invalidateQueries({ queryKey: ["hitl-approvals"] });
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      toast.error(detail ?? "Rejection failed.");
    },
  });

  return (
    <div className="max-w-6xl mx-auto px-6 py-8">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">Production HITL Queue</h1>
          <p className="text-sm text-slate-500 mt-0.5">
            Human-in-the-loop approval requests from deployed agents. Playground approvals are handled inline in the Evaluate tab.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <select
            className="input text-sm w-44"
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
          >
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="timed_out">Timed Out</option>
            <option value="">All</option>
          </select>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="btn-secondary"
          >
            <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </div>

      <div className="rounded-md bg-blue-50 border border-blue-200 px-4 py-2 text-xs text-blue-700 mb-4">
        Showing production approvals only. Sandbox approvals appear in the Evaluate tab during testing.
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-20 text-slate-400">
          <Loader2 size={20} className="animate-spin mr-2" />
          Loading approvals…
        </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 border border-red-200 p-4 text-sm text-red-700">
          Failed to load approvals: {String(error)}
        </div>
      )}

      {data && (
        <div className="card p-0 overflow-hidden">
          {data.items.length === 0 ? (
            <div className="flex flex-col items-center py-16 text-center">
              <CheckCircle size={36} className="text-slate-300 mb-3" />
              <p className="text-slate-500 font-medium">No approvals in this queue</p>
              <p className="text-slate-400 text-sm mt-1">
                Nothing pending right now — auto-refreshes every 30s.
              </p>
            </div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-slate-100 bg-slate-50">
                  {["Agent", "Tool", "Risk", "Context", "Created", "Expires", "Actions"].map(
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
                {data.items.map((ap) => (
                  <>
                    <tr key={ap.id} className="hover:bg-slate-50 transition-colors">
                      <td className="px-4 py-3 font-medium text-slate-800">{ap.agent_name}</td>
                      <td className="px-4 py-3 font-mono text-xs text-slate-600">{ap.tool_name}</td>
                      <td className="px-4 py-3">
                        <span
                          className={`badge ${
                            RISK_CHIP[ap.risk_level] ?? "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {ap.risk_level}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`badge ${
                            CONTEXT_CHIP[ap.context] ?? "bg-slate-100 text-slate-600"
                          }`}
                        >
                          {ap.context}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {new Date(ap.created_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 text-slate-400 text-xs">
                        {new Date(ap.expires_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3">
                        {ap.status === "pending" && (
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => {
                                setApprovingId(approvingId === ap.id ? null : ap.id);
                                setRejectingId(null);
                                setReviewerNotes("");
                              }}
                              className="inline-flex items-center gap-1 text-xs text-green-600 hover:text-green-800 font-medium"
                            >
                              <CheckCircle size={12} />
                              Approve
                            </button>
                            <button
                              onClick={() => {
                                setRejectingId(rejectingId === ap.id ? null : ap.id);
                                setApprovingId(null);
                              }}
                              className="inline-flex items-center gap-1 text-xs text-red-600 hover:text-red-800 font-medium"
                            >
                              <XCircle size={12} />
                              Deny
                            </button>
                          </div>
                        )}
                        {ap.status !== "pending" && (
                          <span className="text-xs text-slate-400">
                            {ap.status} {ap.reviewer_id ? `by ${ap.reviewer_id}` : ""}
                          </span>
                        )}
                      </td>
                    </tr>

                    {/* Inline approve form */}
                    {approvingId === ap.id && (
                      <tr key={`approve-${ap.id}`} className="bg-green-50 border-b border-green-100">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="flex items-end gap-3">
                            <div className="flex-1">
                              <label className="label text-xs mb-1">
                                Reviewer notes (optional)
                              </label>
                              <input
                                className="input text-sm"
                                placeholder="Reason for approval…"
                                value={reviewerNotes}
                                onChange={(e) => setReviewerNotes(e.target.value)}
                              />
                            </div>
                            <button
                              onClick={() =>
                                approveMutation.mutate({ id: ap.id, version: ap.version })
                              }
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

                    {/* Inline reject form */}
                    {rejectingId === ap.id && (
                      <tr key={`reject-${ap.id}`} className="bg-red-50 border-b border-red-100">
                        <td colSpan={7} className="px-4 py-3">
                          <div className="flex items-center gap-3">
                            <span className="text-xs text-red-700">
                              Deny this approval request?
                            </span>
                            <button
                              onClick={() =>
                                rejectMutation.mutate({ id: ap.id, version: ap.version })
                              }
                              disabled={rejectMutation.isPending}
                              className="btn-primary bg-red-600 hover:bg-red-700 text-xs py-2"
                            >
                              {rejectMutation.isPending ? (
                                <Loader2 size={12} className="animate-spin" />
                              ) : (
                                "Confirm Deny"
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
          {data.total} total approval{data.total !== 1 ? "s" : ""} — auto-refreshes every 30s
        </p>
      )}
    </div>
  );
}
