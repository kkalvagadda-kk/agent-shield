import type { ComponentType } from "react";
import { AlertTriangle } from "lucide-react";
import type { DeploymentContext } from "../../api/registryApi";
import OverviewDurable from "./OverviewDurable";
import OverviewEventDriven from "./OverviewEventDriven";
import OverviewReactive from "./OverviewReactive";
import OverviewScheduled from "./OverviewScheduled";

/**
 * WS-6 — the ONE place that answers "which Overview does this operate surface get".
 *
 * THE INVARIANT: every operate surface that renders a shape-specific overview mounts
 * `OverviewForShape`. A page that hand-writes its own shape branch is a parity
 * violation — collapse it here instead of adding a second chain.
 *
 * Why this exists: two pages independently answered this question and drifted.
 * `DeploymentOverviewPage` had a 4-way ternary fallthrough; `CatalogDetailPage` had a
 * hand-written copy that (a) had NO event-driven branch at all and (b) carried a
 * `scheduled` branch that could never fire (see `resolveOverviewShape` below). Both
 * failed SAFE — the catalog page simply rendered less — which is exactly why nobody
 * noticed. Same class as docs/bugs/side-effecting-lost-on-declarative-runner-path.md.
 */

export interface OverviewProps {
  agentName: string;
  deploymentId: string;
  context: DeploymentContext;
}

/**
 * The overview-dispatch shape. NOTE — this is deliberately a SUPERSET of the API's
 * `execution_shape`, and the two must not be confused:
 *
 *   - `execution_shape` is a STORED, 2-value column: `reactive | durable`. It is
 *     CHECK-constrained in the DB (`ck_*_execution_shape`) and pattern-constrained in
 *     Pydantic (`^(reactive|durable)$`).
 *   - `scheduled` and `event_driven` are NOT stored anywhere. They are DERIVED from the
 *     agent's triggers (a schedule trigger / a webhook trigger).
 *
 * This is the distinction the CatalogDetailPage fork got wrong: it dispatched on
 * `config_snapshot.execution_shape` alone, so its `=== "scheduled"` branch was
 * unreachable dead code and event-driven artifacts had no branch to reach.
 * `resolveOverviewShape` is the one place the derivation lives.
 */
export type OverviewShape = "reactive" | "durable" | "scheduled" | "event_driven";

/**
 * Explicit map, NOT a priority chain. A new shape becomes a compile error here
 * (`Record<OverviewShape, ...>` is exhaustive) rather than a silent fallthrough to
 * whatever the last `else` happened to be.
 */
const OVERVIEW_BY_SHAPE: Record<OverviewShape, ComponentType<OverviewProps>> = {
  reactive: OverviewReactive,
  durable: OverviewDurable,
  scheduled: OverviewScheduled,
  event_driven: OverviewEventDriven,
};

/**
 * Derive the overview shape from the signals every operate surface already holds.
 *
 * Precedence (preserved verbatim from DeploymentOverviewPage's original ternary chain,
 * so this refactor changes no behaviour on the page that already worked):
 *   webhook trigger  ⇒ event_driven
 *   schedule trigger ⇒ scheduled
 *   execution_shape === "durable" ⇒ durable
 *   otherwise        ⇒ reactive
 *
 * `executionShape` is typed `string | null | undefined` on purpose: callers read it from
 * an API surface (`agent.execution_shape`) or from a version's `config_snapshot`, where
 * it is an untyped JSON value. Anything that is not exactly `"durable"` is reactive.
 */
export function resolveOverviewShape(signals: {
  hasWebhook: boolean;
  hasSchedule: boolean;
  executionShape: string | null | undefined;
}): OverviewShape {
  if (signals.hasWebhook) return "event_driven";
  if (signals.hasSchedule) return "scheduled";
  return signals.executionShape === "durable" ? "durable" : "reactive";
}

interface Props extends OverviewProps {
  shape: OverviewShape;
}

export default function OverviewForShape({ shape, agentName, deploymentId, context }: Props) {
  // The lookup is typed total, but `shape` can arrive from an API/JSON boundary that
  // TypeScript cannot police. Fail CLOSED and LOUD rather than defaulting to Reactive:
  // a quiet default is precisely how the CatalogDetailPage fork lost event-driven
  // without anyone noticing for months.
  const Component = OVERVIEW_BY_SHAPE[shape] as ComponentType<OverviewProps> | undefined;

  if (!Component) {
    console.error(
      `OverviewForShape: unsupported execution shape "${shape}" for agent "${agentName}". ` +
        `Known shapes: ${Object.keys(OVERVIEW_BY_SHAPE).join(", ")}. ` +
        `Add it to OVERVIEW_BY_SHAPE — do not add a branch at the call site.`
    );
    return (
      <div
        data-testid="overview-unsupported-shape"
        className="card p-5 border-red-200 bg-red-50 flex items-start gap-3"
      >
        <AlertTriangle size={18} className="text-red-500 shrink-0 mt-0.5" />
        <div>
          <p className="text-sm font-semibold text-red-700">
            Unsupported execution shape: {String(shape)}
          </p>
          <p className="text-xs text-red-600 mt-1">
            This operate surface has no overview for that shape. It is a bug, not a
            configuration problem — report it rather than working around it.
          </p>
        </div>
      </div>
    );
  }

  // `data-testid` + `data-shape` are the cross-page parity handle: the same testid
  // renders on the catalog artifact page and the deployment page for the same shape.
  // studio/e2e/catalog-overview-parity.spec.ts asserts that identity, and it cannot
  // pass against a hand-written inline fork (which never rendered this node).
  return (
    <div data-testid="overview-for-shape" data-shape={shape}>
      <Component agentName={agentName} deploymentId={deploymentId} context={context} />
    </div>
  );
}
