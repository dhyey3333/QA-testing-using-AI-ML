import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\checkout.yaml.
// Recorded 2026-10-02T15:50:29 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
  password: "chai-time-42",
  full_name: "Test Shopper",
  address: "12 Example Lane",
  city: "Pune",
  pincode: "411001",
};

test("checkout", async ({ page }) => {
  // log in with the test account
  // add Filter Coffee to the cart
  // open the cart and proceed to checkout
  // fill in the delivery details with the test data
  // choose cash on delivery and place the order
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("button", { name: "Add Filter Coffee (200 g) to cart", exact: true }).click();
  await page.getByRole("link", { name: "Cart (1)", exact: true }).click();
  await page.getByRole("button", { name: "Proceed to checkout", exact: true }).click();
  await page.getByRole("textbox", { name: "Full name", exact: true }).fill(data.full_name);
  await page.getByRole("textbox", { name: "Address", exact: true }).fill(data.address);
  await page.getByRole("textbox", { name: "City", exact: true }).fill(data.city);
  await page.getByRole("textbox", { name: "Pincode", exact: true }).fill(data.pincode);
  await page.getByRole("radio", { name: "Cash on delivery", exact: true }).click();
  await page.getByRole("button", { name: "Place order", exact: true }).click();

  // expected: a page says the order was placed
  // expected: it shows an order number that looks like KC-12345
  // expected: the amount to pay is ₹240
  await expect(page.locator("body")).toContainText("Order placed!");
  await expect(page.locator("body")).toContainText(/Order number: KC-\d+/);
  await expect(page.locator("body")).toContainText("Pay ₹240 in cash when it arrives.");
});
