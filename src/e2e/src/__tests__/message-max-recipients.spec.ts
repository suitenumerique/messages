import test, { expect } from "@playwright/test";
import { resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

test.describe("Message Recipients Limit", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should display max recipients help text for default mailbox", async ({ page, browserName }) => {
    // Navigate to new message form
    const newMessageButton = page.getByRole("link", { name: "New message" });
    await newMessageButton.click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Check that help text shows the limit for the default sender mailbox
    const toFieldHelp = page.locator('text=/maximum 150 for all recipients/i');
    await expect(toFieldHelp).toBeVisible({ timeout: 10000 });
  });

});
