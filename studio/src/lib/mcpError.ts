// ---------------------------------------------------------------------------
// mcpError.ts — extract a human message from an axios error raised by the
// mcp-servers router.
//
// FastAPI's `detail` is a STRING for most 4xx (e.g. name-taken 409) but an
// OBJECT for the delete-blocked 409:
//   { message, blocking_tools: string[], blocking_agents: string[] }
// A naive `String(detail)` renders "[object Object]", hiding exactly the agents
// the operator needs to unbind. This normalizes both shapes in one place.
// ---------------------------------------------------------------------------

interface BlockingDetail {
  message?: string;
  blocking_tools?: string[];
  blocking_agents?: string[];
}

type AxiosLike = { response?: { data?: { detail?: string | BlockingDetail } } };

export function mcpErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as AxiosLike)?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (detail && typeof detail === "object") {
    const { message, blocking_agents } = detail;
    if (message && blocking_agents && blocking_agents.length > 0) {
      return `${message} Bound to: ${blocking_agents.join(", ")}.`;
    }
    if (message) return message;
  }
  return (err as Error)?.message ?? fallback;
}
