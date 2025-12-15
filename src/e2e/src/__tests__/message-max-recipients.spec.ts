import test, { expect } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

test.describe("Message Recipients Limit", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await page.goto("/");
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });

    // Navigate to new message form
    const newMessageButton = page.getByRole("link", { name: "New message" });
    await newMessageButton.click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });
  });

  test("should display max recipients help text for default mailbox", async ({ page, browserName }) => {
    // Check that help text shows the limit for the default sender mailbox
    const toFieldHelp = page.locator('text=/Maximum.*recipients/i');
    await expect(toFieldHelp).toBeVisible({ timeout: 10000 });

    // The help text should contain a number (the limit)
    const helpText = await toFieldHelp.textContent();
    expect(helpText).toMatch(/\d+/); // Should contain at least one digit

    // Extract the limit number from help text
    const limitMatch = helpText?.match(/\d+/);
    expect(limitMatch).toBeTruthy();
    const limit = parseInt(limitMatch![0]);
    // FIXME: this is value set in message-max-recipients-admin.spec.ts
    expect(limit).toBe(50);
  });

});
