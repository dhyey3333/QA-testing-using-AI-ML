import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\add-to-cart.yaml.
// Recorded 2026-10-02T15:47:56 by qwen3-vl:4b-instruct. Re-export after re-recording.

test("add-to-cart", async ({ page }) => {
  // add Masala Chai to the cart
  // add the Clay Kulhad (set of 6) to the cart
  // open the cart
  await page.goto("http://localhost:5180/");
  await page.getByRole("button", { name: "Add Masala Chai (250 g) to cart", exact: true }).click();
  await page.getByRole("button", { name: "Add Clay Kulhad (set of 6) to cart", exact: true }).click();
  await page.getByRole("link", { name: "Cart (2)", exact: true }).click();

  // expected: the cart lists Masala Chai and Clay Kulhad, each with quantity 1
  // expected: the cart prices Masala Chai at ₹180 and Clay Kulhad at ₹420
  // expected: the total equals the sum of the line totals
  // expected: the header's cart link shows 2 items
  await expect(page.locator("body")).toContainText("Masala Chai (250 g) 1 ₹180");
  await expect(page.locator("body")).toContainText("Clay Kulhad (set of 6) 1 ₹420");
  await expect(page.locator("body")).toContainText("Total: ₹600");
  await expect(page.locator("body")).toContainText("₹180");
  await expect(page.locator("body")).toContainText("₹420");
  await expect(page.locator("body")).toContainText("Cart (2)");
});
