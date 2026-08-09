import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, EyeOff, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import {
  listAgentsForTool,
  unpublishTool,
  type RegistryTool,
} from "../../api/registryApi";

/**
 * Confirm taking a tool back out of the org-wide catalog — Decision 47 #4.
 *
 * WHY A DIALOG AND NOT `confirm()`
 * --------------------------------
 * The rest of this page uses `window.confirm` for Delete. That is fine for a question with
 * no content, and wrong here: the whole point of the confirmation is to SHOW the clicker
 * which published agents are still bound, and a native confirm can render neither the list
 * nor the reassurance that goes with it. It is also unassertable — a Vitest cannot read a
 * native dialog's body, so a `confirm()` version of this screen would have no test that
 * could catch it rendering the wrong set.
 *
 * WHAT THE LIST IS FOR — READ THIS BEFORE MAKING IT A GATE
 * --------------------------------------------------------
 * The bound-agent list is a COURTESY. Unpublishing removes discoverability, never
 * capability: an agent already bound to the tool keeps working, because binding is by id
 * and USE is governed by `owner_team` plus grants, never by `publish_status`. So this
 * dialog names them and says so, and the confirm button is never disabled by them.
 *
 * Turning the list into a precondition would assert a dependency that does not exist and
 * would let any team freeze another team's tool in the catalog forever, just by binding it
 * to a published agent. The server agrees by construction — `unpublish_tool` computes the
 * same list and only logs it.
 */
export default function UnpublishToolDialog({
  tool,
  onClose,
}: {
  tool: RegistryTool;
  onClose: () => void;
}) {
  const qc = useQueryClient();

  const { data: bound, isLoading } = useQuery({
    queryKey: ["tool-agents", tool.id],
    queryFn: () => listAgentsForTool(tool.id),
  });

  // Filtered here rather than server-side: `GET /tools/{id}/agents` answers "what uses
  // this", which is the more general question and already had a caller. A publish_status
  // query param would have been a second shape of the same endpoint for one screen.
  const publishedAgents = (bound?.items ?? []).filter(
    (a) => a.publish_status === "published"
  );

  const mutation = useMutation({
    mutationFn: () => unpublishTool(tool.id),
    onSuccess: () => {
      toast.success(`"${tool.display_name ?? tool.name}" is private again.`);
      // Both caches: the row's badge lives in the tools list, and the picker elsewhere
      // reads the same key.
      qc.invalidateQueries({ queryKey: ["registry-tools"] });
      onClose();
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: unknown } } })
        ?.response?.data?.detail;
      // The 403 detail is a sentence naming the owning team; the 409 detail is an
      // object. Surface the sentence when there is one rather than "[object Object]".
      const msg =
        typeof detail === "string"
          ? detail
          : (detail as { error?: string })?.error === "tool_not_published"
            ? "This tool is already private — reload the page."
            : "Failed to unpublish this tool.";
      toast.error(msg);
    },
  });

  const name = tool.display_name ?? tool.name;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 p-4">
      <div
        className="card w-full max-w-lg p-0 overflow-hidden"
        data-testid="unpublish-tool-dialog"
      >
        <div className="flex items-start justify-between border-b border-slate-100 px-5 py-4">
          <div className="flex items-start gap-2">
            <EyeOff size={16} className="text-slate-500 mt-0.5 shrink-0" />
            <div>
              <h2 className="font-semibold text-slate-900">Unpublish “{name}”?</h2>
              <p className="text-xs text-slate-500 mt-0.5">
                It leaves the org-wide catalog and becomes visible only to you.
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Close"
          >
            <X size={16} />
          </button>
        </div>

        <div className="px-5 py-4 space-y-4 text-sm">
          <div
            className="rounded-md bg-slate-50 border border-slate-200 p-3 text-slate-600"
            data-testid="unpublish-effect"
          >
            This changes <strong>discoverability only</strong>. Agents already bound to
            this tool keep calling it — a binding is by id, and permission to call comes
            from the owning team and its grants, not from the catalog.
          </div>

          {isLoading ? (
            <div className="flex items-center gap-2 text-slate-400">
              <Loader2 size={14} className="animate-spin" />
              Checking what still uses it…
            </div>
          ) : publishedAgents.length > 0 ? (
            <div data-testid="unpublish-still-used">
              <p className="flex items-center gap-1.5 font-medium text-amber-700">
                <AlertTriangle size={14} className="shrink-0" />
                {publishedAgents.length} published agent
                {publishedAgents.length === 1 ? "" : "s"} still bound
              </p>
              <ul className="mt-2 space-y-1">
                {publishedAgents.map((a) => (
                  <li
                    key={a.id}
                    className="text-slate-700"
                    data-testid={`unpublish-agent-${a.name}`}
                  >
                    {a.name}
                    <span className="text-slate-400"> · {a.team}</span>
                  </li>
                ))}
              </ul>
              <p className="text-xs text-slate-500 mt-2">
                They are unaffected. Listed so the change is not a surprise.
              </p>
            </div>
          ) : (
            <p className="text-slate-500" data-testid="unpublish-still-used-none">
              No published agent is bound to this tool.
            </p>
          )}

          <p className="text-xs text-slate-500">
            To put it back, publish an agent that binds it — tools go org-wide by riding
            along with an approved agent, never on their own.
          </p>
        </div>

        <div className="flex justify-end gap-2 border-t border-slate-100 px-5 py-3">
          <button onClick={onClose} className="btn-secondary" data-testid="unpublish-cancel">
            Cancel
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            className="btn-primary"
            data-testid="unpublish-confirm"
          >
            {mutation.isPending && <Loader2 size={14} className="animate-spin" />}
            Unpublish
          </button>
        </div>
      </div>
    </div>
  );
}
