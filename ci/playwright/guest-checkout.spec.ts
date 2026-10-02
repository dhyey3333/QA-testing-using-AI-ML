import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\guest-checkout.yaml.
// Recorded 2026-10-02T15:51:37 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
  password: "chai-time-42",
};

test("guest-checkout", async ({ page }) => {
  // without logging in, add Filter Coffee to the cart
  // open the cart and proceed to checkout
  // when asked to log in, log in with the test account
  await page.goto("http://localhost:5180/");
  await page.getByRole("button", { name: "Add Filter Coffee (200 g) to cart", exact: true }).click();
  await page.getByRole("link", { name: "Cart (1)", exact: true }).click();
  await page.getByRole("button", { name: "Proceed to checkout", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Log in", exact: true }).click();

  // expected: the checkout form is shown
  // expected: the order total is ₹240
  await expect(page.locator("body")).toContainText("Checkout");
  await expect(page.locator("body")).toContainText("Order total: ₹240");
});
