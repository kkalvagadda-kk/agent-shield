// e2e/lib/evals.ts — launch an eval against a deployment + wait for it to settle.
import { type APIRequestContext } from "@playwright/test";

/** Launch an eval run (agent or workflow deployment). Returns the eval-run id. */
export async function runEval(
  api: APIRequestContext,
  opts: { datasetId: string; sandboxDeploymentId?: string; workflowDeploymentId?: string },
): Promise<string> {
  const r = await api.post("/api/v1/playground/eval-runs", {
    data: {
      dataset_id: opts.datasetId,
      sandbox_deployment_id: opts.sandboxDeploymentId,
      workflow_deployment_id: opts.workflowDeploymentId,
    },
  });
  if (!r.ok()) throw new Error(`runEval ${r.status()}: ${await r.text()}`);
  return (await r.json()).id as string;
}

/**
 * Poll an eval run to a terminal state. Returns the final run object, or null if it never
 * settled within the budget (no warm eval-runner pod — the accepted boundary).
 */
export async function waitForEvalTerminal(
  api: APIRequestContext, id: string, tries = 40, intervalMs = 3000,
): Promise<Record<string, unknown> | null> {
  for (let i = 0; i < tries; i++) {
    const r = await api.get(`/api/v1/playground/eval-runs/${id}`);
    if (r.ok()) {
      const run = await r.json();
      if (run.status === "completed" || run.status === "failed") return run;
    }
    await new Promise((res) => setTimeout(res, intervalMs));
  }
  return null;
}
