import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\search.yaml.
// Recorded 2026-10-02T15:49:11 by qwen3-vl:4b-instruct. Re-export after re-recording.

test("search", async ({ page }) => {
  // search the shop for "coffee"
  await page.goto("http://localhost:5180/");
  await page.getByRole("searchbox", { name: "Search products", exact: true }).fill("coffee");

  // expected: Filter Coffee (200 g) is shown
  // expected: Masala Chai is not shown
  await expect(page.locator("body")).toContainText("Filter Coffee (200 g)");
  await expect(page.locator("body")).toContainText("80:20 coffee and chicory, ground for a steel filter.");
  await expect(page.locator("body")).toContainText("₹240");
  await expect(page.locator("body")).not.toContainText("Masala Chai");
});
