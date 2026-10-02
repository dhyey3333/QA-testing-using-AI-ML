import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\checkout-validation.yaml.
// Recorded 2026-10-02T15:52:57 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
  password: "chai-time-42",
  full_name: "Test Shopper",
  address: "12 Example Lane",
  city: "Pune",
  bad_pincode: "41100",
};

test("checkout-validation", async ({ page }) => {
  // log in with the test account
  // add Masala Chai to the cart
  // open the cart and proceed to checkout
  // fill in the delivery details from the test data, using bad_pincode (only 5 digits) as the pincode
  // choose cash on delivery and try to place the order
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("button", { name: "Add Masala Chai (250 g) to cart", exact: true }).click();
  await page.getByRole("link", { name: "Cart (1)", exact: true }).click();
  await page.getByRole("button", { name: "Proceed to checkout", exact: true }).click();
  await page.getByRole("textbox", { name: "Pincode", exact: true }).fill(data.bad_pincode);
  await page.getByRole("radio", { name: "Cash on delivery", exact: true }).click();
  await page.getByRole("button", { name: "Place order", exact: true }).click();
  await page.getByRole("textbox", { name: "Full name", exact: true }).fill(data.full_name);
  await page.getByRole("textbox", { name: "Address", exact: true }).fill(data.address);
  await page.getByRole("textbox", { name: "City", exact: true }).fill(data.city);
  await page.getByRole("button", { name: "Place order", exact: true }).click();
  await page.getByRole("button", { name: "Place order", exact: true }).click();
  await page.getByRole("button", { name: "Place order", exact: true }).click();

  // expected: an error message says the pincode must be 6 digits
  // expected: the order was not placed; the checkout form is still shown
  await expect(page.locator("body")).toContainText("Pincode must be 6 digits.");
  await expect(page.locator("body")).toContainText("Checkout");
  await expect(page.locator("body")).toContainText("Place order");
});
