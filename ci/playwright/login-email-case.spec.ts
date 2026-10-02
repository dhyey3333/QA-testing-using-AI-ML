import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\login-email-case.yaml.
// Recorded 2026-10-02T15:46:53 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "Shopper@Kulhad.test",
  password: "chai-time-42",
};

test("login-email-case", async ({ page }) => {
  // open the login page
  // log in with the test account, typing the email exactly as the test data has it (with capital letters)
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByRole("button", { name: "Log in", exact: true }).click();
  await page.getByLabel("Password", { exact: true }).fill(data.password);
  await page.keyboard.press("Enter");

  // expected: the header greets the logged-in user by name
  await expect(page.locator("body")).toContainText("Hi, Test Shopper Log out");
});
