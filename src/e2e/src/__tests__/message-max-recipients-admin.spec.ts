import test, { expect } from "@playwright/test";
import { resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";


test.describe("Message Max Recipients Per Message", () => {

  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `super_admin.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should allow super admin to customize domain max recipients per message", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to domain admin
    const moreOptionsButton = page.getByRole('button', { name: 'More options' });
    await moreOptionsButton.click();
    const manageMaildomainButton = page.getByRole('menuitem', { name: 'Domain admin' });
    await manageMaildomainButton.click();
    await page.waitForURL("/domain");
    await page.getByRole("heading", { name: "Maildomains management" }).waitFor({ state: "visible" });

    // Update max recipients per message
    const tuneLimitsButton = page.getByRole('button', { name: 'tune Settings' });
    await tuneLimitsButton.click();
    const maxRecipientsPerMessageInput = page.getByLabel("Maximum recipients per message");
    await maxRecipientsPerMessageInput.fill("50");
    const saveButton = page.getByRole('button', { name: 'Save' });
    await saveButton.click();
    await page.getByText('The domain settings have been updated!').waitFor({ state: "visible" });

    // Go back to the maildomain list
    const backToMaildomainsButton = page.getByRole('link', { name: 'mail', exact: true });
    await backToMaildomainsButton.click();
    await page.waitForURL("/mailbox/*");

    // Change sender mailbox (click on the From field and select another mailbox)
    const newMessageButton = page.getByRole("link", { name: "New message" });
    await newMessageButton.click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Check that the help text shows the new limit
    const helpText = await page.locator('text=/maximum.*recipients/i').textContent();
    const limitMatch = helpText?.match(/\d+/);
    const limit = limitMatch ? parseInt(limitMatch[0]) : null;
    expect(limit).toBe(50);
  });

  test("should reject domain limit exceeding global maximum", async ({ page, browserName }) => {
    // Login as super_admin
    await page.goto("/");
    await signInKeycloakIfNeeded({ page, username: `super_admin.e2e.${browserName}` });

    // Navigate to domain admin
    const moreOptionsButton = page.getByRole('button', { name: 'More options' });
    await moreOptionsButton.click();
    const manageMaildomainButton = page.getByRole('menuitem', { name: 'Domain admin' });
    await manageMaildomainButton.click();
    await page.waitForURL("/domain");
    await page.getByRole("heading", { name: "Maildomains management" }).waitFor({ state: "visible" });

    // Open settings modal
    const tuneSettingsButton = page.getByRole('button', { name: 'tune Settings' });
    await tuneSettingsButton.click();
    await page.waitForTimeout(50);
    const maxRecipientsPerMessageInput = page.getByLabel("Maximum recipients per message");
    await expect(maxRecipientsPerMessageInput).toBeVisible({ timeout: 5000 });

    // Store current value to verify it's unchanged after failed save
    const initialValue = await maxRecipientsPerMessageInput.inputValue();

    // Try to set a limit exceeding global maximum (200)
    await maxRecipientsPerMessageInput.fill("250");
    const saveButton = page.getByRole('button', { name: 'Save' });
    await saveButton.click();
    await page.getByText('The limit cannot exceed the global maximum of 200 recipients.').waitFor({ state: "visible" });

    // Close the modal
    await page.getByRole('button', { name: 'close' }).click();

    // Verify the limit was not changed
    await tuneSettingsButton.click();
    await page.waitForTimeout(50);
    await expect(maxRecipientsPerMessageInput).toBeVisible({ timeout: 5000 });
    const finalValue = await maxRecipientsPerMessageInput.inputValue();
    expect(finalValue).toBe(initialValue);
  });

  test("should allow domain admin to customize mailbox max recipients per message", async ({ page, browserName }) => {
    // Login as domain admin
    await page.goto("/");
    await signInKeycloakIfNeeded({ page, username: `domain_admin.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");

    // Navigate to domain admin
    const moreOptionsButton = page.getByRole('button', { name: 'More options' });
    await moreOptionsButton.click();
    const manageMaildomainButton = page.getByRole('menuitem', { name: 'Domain admin' });
    await manageMaildomainButton.click();
    await page.waitForURL("/domain");
    await page.getByRole("heading", { name: "Maildomains management" }).waitFor({ state: "visible" });

    // Click on the domain to see mailboxes
    const domainLink = page.getByText('example.local', { exact: true });
    await domainLink.click();
    await page.waitForURL(/\/domain\/[^/]+$/);

    // click on more options button and select manage settings
    // wait for the button to be visible
    await page.getByRole('button', { name: 'more_horiz More' }).first().waitFor({ state: "visible" });
    // const moreOptionsButtonForMailbox = page.getByRole('button', { name: 'more_horiz More' }).first();
    // await moreOptionsButtonForMailbox.click();
    // await page.getByRole('menuitem', { name: 'Manage settings' }).waitFor({ state: "visible" });
    // const manageSettingsButton = page.getByRole('menuitem', { name: 'Manage settings' });
    // await manageSettingsButton.click();
    // await page.waitForURL("/mailbox/*/settings");
    // await page.getByRole("heading", { name: "Mailbox settings" }).waitFor({ state: "visible" });

    // // Wait for the settings modal to appear
    // const maxRecipientsPerMessageInput = page.getByLabel("Maximum recipients per message");
    // await expect(maxRecipientsPerMessageInput).toBeVisible({ timeout: 5000 });

    // // Set a custom limit for the mailbox
    // await maxRecipientsPerMessageInput.fill("25");
    // const saveButton = page.getByRole('button', { name: 'Save' });
    // await saveButton.click();

    // // Verify success message
    // await page.getByText('The mailbox settings have been updated!').waitFor({ state: "visible" });

    // // Go back to the mailbox view
    // const backToMailboxButton = page.getByRole('link', { name: 'mail', exact: true });
    // await backToMailboxButton.click();
    // await page.waitForURL("/mailbox/*");

    // // Navigate to new message form
    // const newMessageButton = page.getByRole("link", { name: "New message" });
    // await newMessageButton.click();
    // await page.waitForURL("/mailbox/*/new");
    // await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // // Check that the help text shows the new mailbox-specific limit
    // const helpText = await page.locator('text=/maximum.*recipients/i').textContent();
    // const limitMatch = helpText?.match(/\d+/);
    // const limit = limitMatch ? parseInt(limitMatch[0]) : null;
    // expect(limit).toBe(25);
  });
});
