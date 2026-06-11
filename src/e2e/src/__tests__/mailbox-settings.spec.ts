import test, { expect } from "@playwright/test";
import { resetDatabase } from "../utils";
import { openMailboxSettingsModal, signInKeycloakIfNeeded } from "../utils-test";

// The per-mailbox configuration views (rename, access sharing, signatures,
// templates, auto-replies, integrations) now live inside a single "Settings"
// modal opened from the header's "More options" menu. These tests cover the
// modal shell itself: renaming the mailbox (General tab), the mailbox switcher
// (which mailboxes the current user is allowed to configure) and the set of
// tabs exposed for an administrator.

test.describe("Mailbox settings modal", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should rename the mailbox from the General tab", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    const modal = await openMailboxSettingsModal(page);

    // The administered mailbox opens on the General tab by default, where the
    // sender name can be edited.
    await expect(modal.getByRole("tab", { name: "General" })).toBeVisible();
    await expect(modal.getByText("Customize your sender name")).toBeVisible();

    const nameInput = modal.getByRole("textbox", { name: "Name" });
    await nameInput.fill("Renamed Mailbox E2E");

    // The Validate button is enabled only once the field is dirty.
    const validateButton = modal.getByRole("button", { name: "Validate" });
    await expect(validateButton).toBeEnabled();
    await validateButton.click();

    await expect(page.getByText("The mailbox name has been updated!")).toBeVisible();
    // The form keeps the new value after a successful save.
    await expect(nameInput).toHaveValue("Renamed Mailbox E2E");
  });

  test("should reject an empty mailbox name", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    const modal = await openMailboxSettingsModal(page);

    const nameInput = modal.getByRole("textbox", { name: "Name" });
    await nameInput.fill("");

    await modal.getByRole("button", { name: "Validate" }).click();

    // Client-side zod validation blocks the submission and surfaces the error.
    await expect(modal.getByText("Name is required.")).toBeVisible();
    await expect(page.getByText("The mailbox name has been updated!")).not.toBeVisible();
  });

  test("should only list mailboxes the user can configure in the switcher", async ({
    page,
    browserName,
  }) => {
    await page.waitForLoadState("networkidle");

    const modal = await openMailboxSettingsModal(page);

    // The user administers two mailboxes (their own + the import mailbox) and is
    // only a sender on the shared mailbox, so the switcher is rendered and lists
    // exactly the two administered mailboxes.
    //
    // The switcher trigger is the shared MailboxSelector card: its accessible
    // name is the currently-configured mailbox (the user's own administered
    // mailbox) rather than a fixed label.
    await modal
      .getByRole("button", { name: `user.e2e.${browserName}@example.local` })
      .click();

    // The switcher renders a single-select dropdown, so its entries expose a
    // `menuitemradio`/`option` role depending on the design-system version; match
    // both so the assertion does not hinge on that internal detail. Scope to the
    // dropdown popover: it is portalled outside the modal, and the thread list is
    // itself a listbox whose `option` entries would otherwise be matched too.
    const switcherPopover = page.getByRole("menu");
    const options = switcherPopover.locator('[role^="menuitem"], [role="option"]');
    await expect(options).toHaveCount(2);
    await expect(
      options.filter({ hasText: `user.e2e.${browserName}@example.local` }),
    ).toBeVisible();
    await expect(
      options.filter({ hasText: "import.e2e@example.local" }),
    ).toBeVisible();
    // The shared mailbox (sender role only) cannot be configured and is excluded.
    await expect(
      options.filter({ hasText: "shared.e2e@example.local" }),
    ).toHaveCount(0);
  });

  test("should expose every settings tab for an administered mailbox", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    const modal = await openMailboxSettingsModal(page);

    // An admin (manage_accesses + manage_message_templates) sees the full set.
    await expect(modal.getByRole("tab", { name: "General" })).toBeVisible();
    await expect(modal.getByRole("tab", { name: "Access sharing" })).toBeVisible();
    await expect(
      modal.getByRole("tab", { name: "Message templates" }),
    ).toBeVisible();
    await expect(modal.getByRole("tab", { name: "Auto-replies" })).toBeVisible();
    await expect(modal.getByRole("tab", { name: "Signatures" })).toBeVisible();
  });
});
