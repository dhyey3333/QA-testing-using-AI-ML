import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\remove-from-cart.yaml.
// Recorded 2026-10-02T15:48:53 by qwen3-vl:4b-instruct. Re-export after re-recording.

test("remove-from-cart", async ({ page }) => {
  // add Masala Chai to the cart
  // add Filter Coffee to the cart
  // open the cart
  // remove Masala Chai from the cart
  await page.goto("http://localhost:5180/");
  await page.getByRole("button", { name: "Add Masala Chai (250 g) to cart", exact: true }).click();
  await page.getByRole("button", { name: "Add Filter Coffee (200 g) to cart", exact: true }).click();
  await page.getByRole("link", { name: "Cart (2)", exact: true }).click();
  await page.getByRole("button", { name: "Remove Masala Chai (250 g)", exact: true }).click();

  // expected: the cart lists Filter Coffee
  // expected: Masala Chai is no longer in the cart
  // expected: the total is ₹240
  await expect(page.locator("body")).toContainText("Filter Coffee (200 g)");
  await expect(page.locator("body")).toContainText("Total: ₹240");
  await expect(page.locator("body")).not.toContainText("Masala Chai");
});
