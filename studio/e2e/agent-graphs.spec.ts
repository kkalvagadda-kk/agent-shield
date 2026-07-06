import { test, expect } from "@playwright/test";

// ---------------------------------------------------------------------------
// agent-graphs.spec.ts
//   Proves the Agent Graphs list page and the canvas (/agent-graphs/new) render.
//
//   "Agent Graphs" is the renamed visual canvas list (previously "Workflows"
//   canvas before Decision 22 renamed the old canvas to "agent graphs" and
//   introduced composite Workflows for multi-agent pipelines).
// ---------------------------------------------------------------------------

test.describe("agent graphs list", () => {
  test("renders heading and New Agent Graph button", async ({ page }) => {
    await page.goto("/agent-graphs");
    await page.waitForLoadState("networkidle");

    await expect(
      page.getByRole("heading", { name: /Agent Graphs/i }).first()
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /New Agent Graph/i })
    ).toBeVisible();
  });

  test("renders table or empty-state (no crash)", async ({ page }) => {
    await page.goto("/agent-graphs");
    await page.waitForLoadState("networkidle");

    // Either the table (with column headers) or the empty-state card is visible
    const tableCount = await page.locator("table").count();
    const emptyCount = await page.getByText(/No agent graphs yet/i).count();
    expect(tableCount + emptyCount).toBeGreaterThan(0);
  });

  // "Agent Graphs" is intentionally hidden from the sidebar nav (composite
  // Workflow builder supersedes it); the route stays reachable by direct URL.
  test("agent-graphs is hidden from the nav but reachable by URL", async ({
    page,
  }) => {
    await page.goto("/");
    await page.waitForLoadState("networkidle");
    // Nav link should NOT be present.
    await expect(page.getByRole("link", { name: /^Agent Graphs$/i })).toHaveCount(0);

    // Direct navigation still works.
    await page.goto("/agent-graphs");
    await page.waitForLoadState("networkidle");
    await expect(
      page.getByRole("heading", { name: /Agent Graphs/i }).first()
    ).toBeVisible();
  });
});

test.describe("canvas page (/agent-graphs/new)", () => {
  test("New Agent Graph button navigates to canvas and Toolbar renders", async ({
    page,
  }) => {
    await page.goto("/agent-graphs");
    await page.waitForLoadState("networkidle");

    await page.getByRole("button", { name: /New Agent Graph/i }).click();
    await page.waitForURL("**/agent-graphs/new", { timeout: 10_000 });
    await page.waitForLoadState("networkidle");

    // Canvas.tsx renders a Toolbar with "Agent", "End", "Save", and "Deploy"
    // buttons.  All four should be visible on a new empty canvas.
    await expect(
      page.getByRole("button", { name: /\+ Agent/i })
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: /\+ End/i })
    ).toBeVisible();
    await expect(page.getByRole("button", { name: /Save/i })).toBeVisible();
    await expect(
      page.getByRole("button", { name: /Deploy/i })
    ).toBeVisible();
  });

  test("adding an Agent node to the canvas renders it", async ({ page }) => {
    await page.goto("/agent-graphs/new");
    await page.waitForLoadState("networkidle");

    // The ReactFlow canvas is rendered
    await expect(page.locator(".react-flow")).toBeVisible();

    // Click "+ Agent" to add a node
    await page.getByRole("button", { name: /\+ Agent/i }).click();

    // A new AgentNode should appear in the canvas
    await expect(
      page.locator(".react-flow__node")
    ).toBeVisible({ timeout: 10_000 });
  });

  test("direct navigation to /agent-graphs/new renders canvas", async ({
    page,
  }) => {
    await page.goto("/agent-graphs/new");
    await page.waitForLoadState("networkidle");

    // ReactFlow mounts the canvas element
    await expect(page.locator(".react-flow")).toBeVisible();

    // Toolbar is present
    await expect(
      page.getByRole("button", { name: /\+ Agent/i })
    ).toBeVisible();
  });
});
