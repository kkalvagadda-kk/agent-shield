// e2e/lib/catalog.ts — marketplace deploy + consumer chat.
import { expect, type Page, type APIRequestContext } from "@playwright/test";

/** Find the catalog artifact id for a source agent name (owner-team scoped list). */
export async function findCatalogArtifact(api: APIRequestContext, agentName: string): Promise<string | null> {
  const r = await api.get("/api/v1/catalog");
  if (!r.ok()) return null;
  const hit = (await r.json()).find((a: { name?: string; source_name?: string; id: string }) =>
    a.name === agentName || a.source_name === agentName,
  );
  return hit ? hit.id : null;
}

/**
 * Deploy the latest catalog version → ProductionDeployment. Returns the 201 status.
 * Reference: CatalogDetailPage "Deploy Latest".
 */
export async function deployFromCatalog(page: Page, artifactId: string): Promise<number> {
  await page.goto(`/catalog/${artifactId}`);
  const deployed = page.waitForResponse(
    (r) => r.request().method() === "POST" && new RegExp(`/api/v1/catalog/${artifactId}/deploy$`).test(r.url()),
  );
  await page.getByRole("button", { name: /Deploy Latest/i }).click();
  return (await deployed).status();
}

/** Open the consumer chat surface for a catalog artifact (context="production"). */
export async function openConsumerChat(page: Page, artifactId: string): Promise<void> {
  await page.goto(`/catalog/${artifactId}/chat`);
  await expect(page.getByRole("textbox").first()).toBeVisible({ timeout: 15_000 });
}
