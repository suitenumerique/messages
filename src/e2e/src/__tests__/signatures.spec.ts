import test, { expect, Locator, Page } from "@playwright/test";
import { resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

// Helper to click checkbox and wait for toast to be visible
const clickAndWaitForToast = async (page: Page, checkbox: Locator) => {
  await checkbox.click({ force: true });
  // Toast should be visible and closable
  const toast = page.getByText("Signature updated!close").first()
  await expect(toast).toBeVisible();
  await toast.getByRole("button", { name: "Close" }).click();
};

test.describe("Mailbox Signatures", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should show empty state when no signatures exist", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Wait for the data grid to load
    await page.waitForLoadState("networkidle");

    // Check if empty state message exists (may or may not be visible depending on existing data)
    const emptyLabel = page.getByText("No signatures found");
    // There are no signatures, the empty label should be visible
    await expect(emptyLabel).toBeVisible();
  });
  test("should create a new signature", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Open the settings dropdown menu
    const settingsButton = page.getByRole("button", { name: "More options" });
    await expect(settingsButton).toBeVisible();
    await settingsButton.click();

    // Click on Signatures menu item
    const signaturesMenuItem = page.getByRole("menuitem", { name: "Signatures" });
    await expect(signaturesMenuItem).toBeVisible();
    await signaturesMenuItem.click();

    // Verify we are on the signatures page
    await page.waitForURL("**/mailbox/*/signatures");
    const pageTitle = page.getByRole("heading", { name: "Signatures", level: 1 });
    await expect(pageTitle).toBeVisible();

    // Click on New signature button
    const newSignatureButton = page.getByRole("button", { name: "New signature" });
    await expect(newSignatureButton).toBeVisible();
    await newSignatureButton.click();

    // Fill in the signature form
    const modal = page.getByRole("dialog");
    await expect(modal).toBeVisible();
    await expect(modal.getByText("Create a new signature")).toBeVisible();

    // Fill name
    await modal.getByRole("textbox", { name: "Name" }).fill("E2E Test Signature");

    // Fill signature content in the editor
    await modal.locator(".ProseMirror").click();
    await modal.locator(".ProseMirror").pressSequentially("Best regards,\nE2E Test User");

    // Save the signature
    await modal.getByRole("button", { name: "Save" }).click();

    // Verify success toast
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Verify signature appears in the list
    await expect(page.getByRole("cell", { name: "E2E Test Signature" })).toBeVisible();

  });
  test("should edit a signature", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // First create a signature to edit
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Signature to Edit");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Original content");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Wait for the signature to appear in the list
    await expect(page.getByRole("cell", { name: "Signature to Edit" })).toBeVisible();

    // Click Modify button on the signature row
    const signatureRow = page.getByRole("row", { name: /Signature to Edit/ });
    await signatureRow.getByRole("button", { name: "Modify" }).click();

    // Edit the signature
    const editModal = page.getByRole("dialog");
    await expect(editModal).toBeVisible();
    await expect(editModal.getByText('Edit signature "Signature to Edit"')).toBeVisible();

    // Clear and update the name
    await editModal.getByRole("textbox", { name: "Name" }).clear();
    await editModal.getByRole("textbox", { name: "Name" }).fill("Signature Edited");

    // Save changes
    await editModal.getByRole("button", { name: "Save" }).click();

    // Verify success toast
    await expect(page.getByText("Signature updated!")).toBeVisible();

    // Verify updated signature appears in the list
    await expect(page.getByRole("cell", { name: "Signature Edited" })).toBeVisible();
  });

  test("should delete a signature", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // First create a signature to delete
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Signature to Delete");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("This will be deleted");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Wait for the signature to appear in the list
    await expect(page.getByRole("cell", { name: "Signature to Delete" })).toBeVisible();

    // Click Delete button on the signature row
    const signatureRow = page.getByRole("row", { name: /Signature to Delete/ });
    await signatureRow.getByRole("button", { name: "Delete" }).click();

    // Confirm deletion in the confirmation modal
    const confirmModal = page.getByRole("dialog");
    await expect(confirmModal).toBeVisible();
    await expect(confirmModal.getByText(/Are you sure you want to delete this signature/)).toBeVisible();
    await confirmModal.getByRole("button", { name: "Delete" }).click();

    // Verify success toast
    await expect(page.getByText("Signature deleted!")).toBeVisible();

    // Verify signature is removed from the list
    await expect(page.getByRole("cell", { name: "Signature to Delete" })).not.toBeVisible();
  });

  test("should set a signature as default via checkbox", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Create a signature
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Default Signature Test");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("This is my default signature");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Wait for the signature to appear in the list
    await expect(page.getByRole("cell", { name: "Default Signature Test" })).toBeVisible();

    // Find the signature row and click the default checkbox
    const signatureRow = page.getByRole("row", { name: /Default Signature Test/ });
    const defaultCheckbox = signatureRow.getByRole("checkbox", { name: "Default" });
    await expect(defaultCheckbox).not.toBeChecked();
    await defaultCheckbox.click();

    // Verify success toast
    await expect(page.getByText("Signature updated!")).toBeVisible();

    // Verify checkbox is now checked
    await expect(defaultCheckbox).toBeChecked();
  });

  test("should create a signature as default via modal checkbox", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Create a signature with default checkbox checked
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Modal Default Signature");

    // Check the default signature checkbox in the modal
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();

    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Created as default");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Verify the signature appears and is marked as default
    const signatureRow = page.getByRole("row", { name: /Modal Default Signature/ });
    await expect(signatureRow).toBeVisible();
    const defaultCheckbox = signatureRow.getByRole("checkbox", { name: "Default" });
    await expect(defaultCheckbox).toBeChecked();
  });

  test("should load default signature when composing a new message", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Create a default signature
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Auto Load Signature");
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("-- Auto loaded signature content --");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Go back to inbox
    await page.getByRole("link", { name: /Inbox/ }).click();
    await page.waitForURL("**/mailbox/*");

    // Click new message button
    const newMessageButton = page.getByRole('link', { name: 'New message' })
    await newMessageButton.click();

    // Wait for the composer to load
    await page.waitForLoadState("networkidle");

    // Verify the default signature is loaded in the editor
    const editor = page.locator(".ProseMirror");
    await expect(editor).toContainText("-- Auto loaded signature content --");
  });

  test("should only have one default signature at a time", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to signatures page
    const settingsButton = page.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Create first signature and set as default
    await page.getByRole("button", { name: "New signature" }).click();
    let createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("First Default");
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("First signature");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Create second signature and set as default
    await page.getByRole("button", { name: "New signature" }).click();
    createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Second Default");
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Second signature");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Verify only second signature is default (first should be unchecked)
    const firstRow = page.getByRole("row", { name: /First Default/ });
    const secondRow = page.getByRole("row", { name: /Second Default/ });

    await expect(firstRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();
    await expect(secondRow.getByRole("checkbox", { name: "Default" })).toBeChecked();
  });

  test("should prioritize mailbox default signature over maildomain default signature", async ({ page, browserName }) => {
    // This test verifies that when both mailbox and domain have default signatures,
    // the mailbox default signature takes priority.
    // We create the mailbox signature first, then the domain signature, to ensure
    // the priority is based on scope (mailbox > domain), not creation order.

    // Clear existing session
    await page.context().clearCookies();

    // STEP 1: Login as regular user and create a DEFAULT mailbox signature
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");

    // Navigate to mailbox signatures page
    const userSettingsButton = page.getByRole("button", { name: "More options" });
    await userSettingsButton.click();
    await page.getByRole("menuitem", { name: "Signatures" }).click();
    await page.waitForURL("**/mailbox/*/signatures");

    // Create a mailbox signature WITH is_default
    await page.getByRole("button", { name: "New signature" }).click();
    let createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Mailbox Default Sig");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("-- MAILBOX SIGNATURE --");
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // STEP 2: Login as domain admin and create a DEFAULT domain signature
    await page.context().clearCookies();
    await signInKeycloakIfNeeded({ page, username: `domain_admin.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");

    // Navigate to domain admin
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Domain admin" }).click();
    await page.waitForURL("**/domain");

    // Click on the first domain
    const domainRow = page.getByRole("row").nth(1); // First data row
    await domainRow.click();
    await page.waitForURL("**/domain/*");

    // Navigate to signatures tab
    await page.getByRole("link", { name: "Signatures" }).click();
    await page.waitForURL("**/domain/*/signatures");

    // Create a default signature at maildomain level
    await page.getByRole("button", { name: "New signature" }).click();
    createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Maildomain Default Sig");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("-- MAILDOMAIN SIGNATURE --");
    await createModal.getByRole("checkbox", { name: "Default signature" }).click();
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // STEP 3: Go back to mailbox as regular user and verify MAILBOX signature is used
    await page.context().clearCookies();
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");

    // Click new message button
    const newMessageButton = page.getByRole("link", { name: "New message" });
    await newMessageButton.click();

    // Wait for the composer to load
    await page.waitForLoadState("networkidle");

    // Verify the MAILBOX signature is loaded (mailbox default takes priority over domain default)
    const editor = page.locator(".ProseMirror");
    await expect(editor).toContainText("-- MAILBOX SIGNATURE --");
    await expect(editor).not.toContainText("-- MAILDOMAIN SIGNATURE --");
  });

});

test.describe("Maildomain Signatures (Admin)", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `domain_admin.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should toggle is_active, is_default, and is_forced checkboxes", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to domain admin
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Domain admin" }).click();
    await page.waitForURL("**/domain");

    // Click on the first domain
    const domainRow = page.getByRole("row").nth(1);
    await domainRow.click();
    await page.waitForURL("**/domain/*");

    // Navigate to signatures tab
    await page.getByRole("link", { name: "Signatures" }).click();
    await page.waitForURL("**/domain/*/signatures");

    // Create a new signature
    await page.getByRole("button", { name: "New signature" }).click();
    const createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Test Toggle Signature");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Signature content for toggle test");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Wait for the signature to appear in the list
    const signatureRow = page.getByRole("row", { name: /Test Toggle Signature/ });
    await expect(signatureRow).toBeVisible();

    // Get the checkboxes
    const activeCheckbox = signatureRow.getByRole("checkbox", { name: "Active" });
    const defaultCheckbox = signatureRow.getByRole("checkbox", { name: "Default" });
    const forcedCheckbox = signatureRow.getByRole("checkbox", { name: "Forced" });

    // Initial state: Active is checked by default, Default and Forced are not
    await expect(activeCheckbox).toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Default ON
    await clickAndWaitForToast(page, defaultCheckbox);
    await expect(defaultCheckbox).toBeChecked();

    // Toggle Default OFF
    await clickAndWaitForToast(page, defaultCheckbox);
    await expect(defaultCheckbox).not.toBeChecked();

    // Toggle Forced ON
    await clickAndWaitForToast(page, forcedCheckbox);
    await expect(forcedCheckbox).toBeChecked();

    // Toggle Forced OFF
    await clickAndWaitForToast(page, forcedCheckbox);
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Active OFF
    await clickAndWaitForToast(page, activeCheckbox);
    await expect(activeCheckbox).not.toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Active ON again
    await clickAndWaitForToast(page, activeCheckbox);
    await expect(activeCheckbox).toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Default and Forced ON again
    await clickAndWaitForToast(page, defaultCheckbox);
    await expect(defaultCheckbox).toBeChecked();
    await clickAndWaitForToast(page, forcedCheckbox);
    await expect(forcedCheckbox).toBeChecked();

    // Toggle Active OFF again - Default and Forced should be unchecked
    await clickAndWaitForToast(page, activeCheckbox);
    await expect(activeCheckbox).not.toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Force ON again - Forced AND Active should be enabled again
    await clickAndWaitForToast(page, forcedCheckbox);
    await expect(forcedCheckbox).toBeChecked();
    await expect(activeCheckbox).toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();

    // Toggle Active OFF again - Default and Forced should be unchecked
    await clickAndWaitForToast(page, activeCheckbox);
    await expect(activeCheckbox).not.toBeChecked();
    await expect(defaultCheckbox).not.toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();

    // Toggle Default ON again - Default AND Active should be enabled again
    await clickAndWaitForToast(page, defaultCheckbox);
    await expect(defaultCheckbox).toBeChecked();
    await expect(activeCheckbox).toBeChecked();
    await expect(forcedCheckbox).not.toBeChecked();
  });

  test("should only have one default signature at a time", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to domain admin
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await page.getByRole("menuitem", { name: "Domain admin" }).click();
    await page.waitForURL("**/domain");

    // Click on the first domain
    const domainRow = page.getByRole("row").nth(1);
    await domainRow.click();
    await page.waitForURL("**/domain/*");

    // Navigate to signatures tab
    await page.getByRole("link", { name: "Signatures" }).click();
    await page.waitForURL("**/domain/*/signatures");

    // Create first signature
    await page.getByRole("button", { name: "New signature" }).click();
    let createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("First Signature");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("First signature content");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Create second signature
    await page.getByRole("button", { name: "New signature" }).click();
    createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Second Signature");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Second signature content");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!")).toBeVisible();

    // Create third signature
    await page.getByRole("button", { name: "New signature" }).click();
    createModal = page.getByRole("dialog");
    await createModal.getByRole("textbox", { name: "Name" }).fill("Third Signature");
    await createModal.locator(".ProseMirror").click();
    await createModal.locator(".ProseMirror").pressSequentially("Third signature content");
    await createModal.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("Signature created!").last()).toBeVisible();

    // Get signature rows
    const firstRow = page.getByRole("row", { name: /First Signature/ });
    const secondRow = page.getByRole("row", { name: /Second Signature/ });
    const thirdRow = page.getByRole("row", { name: /Third Signature/ });

    // Signatures are already active by default, verify Default checkboxes are enabled
    await expect(firstRow.getByRole("checkbox", { name: "Default" })).toBeEnabled();
    await expect(secondRow.getByRole("checkbox", { name: "Default" })).toBeEnabled();
    await expect(thirdRow.getByRole("checkbox", { name: "Default" })).toBeEnabled();

    // Set first signature as default
    await clickAndWaitForToast(page, firstRow.getByRole("checkbox", { name: "Default" }));

    // Verify only first is default
    await expect(firstRow.getByRole("checkbox", { name: "Default" })).toBeChecked();
    await expect(secondRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();
    await expect(thirdRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();

    // Set second signature as default
    await clickAndWaitForToast(page, secondRow.getByRole("checkbox", { name: "Default" }));

    // Verify only second is default (first should be unchecked now)
    await expect(firstRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();
    await expect(secondRow.getByRole("checkbox", { name: "Default" })).toBeChecked();
    await expect(thirdRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();

    // Set third signature as default
    await clickAndWaitForToast(page, thirdRow.getByRole("checkbox", { name: "Default" }));

    // Verify only third is default
    await expect(firstRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();
    await expect(secondRow.getByRole("checkbox", { name: "Default" })).not.toBeChecked();
    await expect(thirdRow.getByRole("checkbox", { name: "Default" })).toBeChecked();
  });

});
