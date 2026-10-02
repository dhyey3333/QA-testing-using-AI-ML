import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\logout.yaml.
// Recorded 2026-10-02T15:46:24 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
  password: "chai-time-42",
};

test("logout", async ({ page }) => {
  // log in with the test account
  // log out
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByRole("button", { name: "Log out", exact: true }).click();

  // expected: the header shows a "Log in" link again
  // expected: the header no longer greets the user by name
  await expect(page.locator("body")).toContainText("Log in");
  await expect(page.locator("body")).toContainText("Kulhad & Co.");
  await expect(page.locator("body")).not.toContainText("User name");
});
