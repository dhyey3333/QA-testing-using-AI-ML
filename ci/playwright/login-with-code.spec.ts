import { test, expect } from "@playwright/test";

// Exported by Nightshift on 2026-10-02 from specs\login-with-code.yaml.
// Recorded 2026-10-02T15:47:25 by qwen3-vl:4b-instruct. Re-export after re-recording.

const data = {
  email: "shopper@kulhad.test",
};

test("login-with-code", async ({ page }) => {
  // open the login page and choose to sign in with an email code
  // type the test account's email and ask for a code
  // type the code from the email, {{email_code}}, and sign in
  await page.goto("http://localhost:5180/");
  await page.getByRole("link", { name: "Log in", exact: true }).click();
  await page.getByRole("link", { name: "Sign in with an email code instead", exact: true }).click();
  await page.getByRole("textbox", { name: "Email", exact: true }).fill(data.email);
  await page.getByRole("button", { name: "Email me a code", exact: true }).click();
  await page.getByRole("textbox", { name: "Sign-in code", exact: true }).fill(data.email_code);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();

  // expected: the header greets the logged-in user by name
  await expect(page.locator("body")).toContainText("Hi, Test Shopper Log out");
});
