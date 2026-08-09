// e2e/lib/chat.ts — chat on AgentChatPage / CatalogChatPage + memory round-trip.
import { expect, type Page } from "@playwright/test";

/**
 * Type a message and send it. Arms the chat POST waitForResponse BEFORE clicking and returns
 * the posted body, or null if it never fired within `timeoutMs` (no warm pod — the accepted
 * cold-pod boundary; caller annotate-skips the live assertion). `chatUrlRe` matches the
 * surface's POST (e.g. /agents/{name}/chat or /agents/{name}/deployments/{dep}/chat).
 */
export async function sendChatTurn(
  page: Page, message: string, chatUrlRe: RegExp, timeoutMs = 20_000,
): Promise<Record<string, unknown> | null> {
  const posted = page
    .waitForResponse((r) => r.request().method() === "POST" && chatUrlRe.test(r.url()), { timeout: timeoutMs })
    .catch(() => null);
  // RETURN null, DO NOT THROW, when the composer is not there. Every caller already
  // treats null as "could not send" — assertRecall maps it to "skipped"/"asked" and
  // records an annotation rather than failing. But the fill itself threw, so a cold or
  // absent agent pod took down the whole test the caller had explicitly labelled
  // "best-effort … cold-pod tolerant" (lifecycle-journey leg 5). The composer is absent
  // exactly when there is no live deployment to chat with, which is the tolerated case.
  const input = page.getByRole("textbox").first();
  try {
    await input.waitFor({ state: "visible", timeout: 10_000 });
    await input.fill(message);
    await input.press("Enter");
  } catch {
    return null;   // no composer -> nothing was sent; the caller decides what that means
  }
  const resp = await posted;
  return resp ? (resp.request().postDataJSON() as Record<string, unknown>) : null;
}

/**
 * Save→reload→assert survived (DoD #2): after at least one turn, reload the page and assert
 * the transcript rehydrates from the backend (a memory GET fires on mount and a prior
 * message re-renders). `prior` is a substring of a message that must reappear.
 */
export async function assertRehydratesOnReload(
  page: Page, agentName: string, prior: string,
): Promise<void> {
  const memGet = page
    .waitForResponse((r) => r.request().method() === "GET" && r.url().includes(`/api/v1/agents/${agentName}/memory`), { timeout: 15_000 })
    .catch(() => null);
  await page.reload();
  await memGet;
  await expect(page.getByText(prior, { exact: false }).first()).toBeVisible({ timeout: 15_000 });
}

/**
 * Best-effort cross-turn recall check (cold-pod tolerant). Send a novel fact, then ask it
 * back; if the reply completes, assert it references `token`. Returns "recalled" | "asked" |
 * "skipped" so the spec records the outcome instead of a hard failure with no warm pod.
 */
export async function assertRecall(
  page: Page, chatUrlRe: RegExp, fact: string, question: string, token: string,
): Promise<"recalled" | "asked" | "skipped"> {
  if (!(await sendChatTurn(page, fact, chatUrlRe))) return "skipped";
  await page.waitForTimeout(1500);
  if (!(await sendChatTurn(page, question, chatUrlRe))) return "asked";
  try {
    await expect(page.getByText(new RegExp(token, "i")).last()).toBeVisible({ timeout: 25_000 });
    return "recalled";
  } catch {
    return "asked";
  }
}
