import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\login.yaml.
// Recorded 2026-10-02T15:45:59 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
  password: "chai-time-42",
};

test("login", async ({ page }) => {
  // open the login page
  // log in with the test account
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Log in", exact: true }).click();

  // expected: the header greets the logged-in user by name
  await expect(page.locator("body")).toContainText("Hi, Test Shopper Log out");
});
