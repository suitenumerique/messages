import { test, expect } from "@playwright/test";
import { FIXTURES_PATH } from "../constants";
import path from "path";
import { getMailboxEmail, resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

test.describe("Import Message", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should import an eml archive file", async ({ page, browserName }) => {
    // The archive is uploaded to object storage, then imported by a Celery
    // worker the modal polls — more than the default 30s budget allows.
    test.setTimeout(60_000);
    const email = `import.e2e@example.local`;
    await page.waitForLoadState("networkidle");

    // Go the import mailbox
    await page.getByRole("button", { name: getMailboxEmail('user', browserName) }).click();
    await page.getByRole("menuitem").filter({ hasText: getMailboxEmail('import') }).click();
    await page.waitForLoadState("networkidle");

    // As the database is fresh, there should be no threads and the Import messages button should be visible
    const noThreads = page.getByText("No threads");
    await expect(page.getByRole("link", { name: "Import messages" })).toBeVisible();

    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();

    const menuItem = page.getByRole("menuitem", { name: "Import messages" });
    await menuItem.click();

    const importModal = page.getByRole("dialog");
    const modalTitle = importModal.locator(".c__modal__title");
    expect(await modalTitle.textContent()).toBe(
      `Import your old messages in ${email}`
    );

    const fileInput = page.locator('input[type="file"][name="archive_file"]');

    // Import a wrong file type should show an error
    const importButton = page.getByRole("button", { name: "Import" });
    await fileInput.setInputFiles(path.join(FIXTURES_PATH, "attachment.png"));
    await importButton.click();

    const errorBanner = page.getByRole("alert", {
      name: "An error occurred while uploading the archive file.",
    });
    await errorBanner.waitFor({ state: "visible" });

    await fileInput.setInputFiles(path.join(FIXTURES_PATH, "old-message.eml"));

    // Armed before the click: the archive is small enough that the upload and
    // the import-run creation can both land before a post-click listener would
    // be attached.
    const importRunPromise = page.waitForResponse(
      (response) =>
        /\/api\/v1\.0\/mailboxes\/[^/]+\/imports\/$/.test(response.url()) &&
        response.request().method() === "POST" &&
        response.status() === 202
    );

    await importButton.click();
    await expect(errorBanner).not.toBeVisible();

    // The archive goes to object storage first (presigned PUT), then
    // POST /mailboxes/{id}/imports/ creates the run the modal polls.
    await importRunPromise;

    await expect(page.getByText("Importing...")).toBeVisible();

    // Completion is asserted on the UI rather than on a polling response: the
    // modal stops polling on the first terminal status, so racing that single
    // response would be flaky.
    // New completion UI: badge + heading + per-archive stats.
    await expect(page.getByText("Import complete")).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("100% imported")).toBeVisible();
    await expect(
      page.getByText("Imported: 1 of 1 messages")
    ).toBeVisible();
    // A single-message archive must not trip the failure warning.
    await expect(page.getByLabel("High failure rate")).toHaveCount(0);

    const closeButton = page.getByRole("button", {
      name: "Close",
      exact: true,
    });
    await closeButton.click();

    await importModal.waitFor({ state: "hidden" });

    // Then expect the new message to be visible in the thread list
    await expect(
      page.getByRole("option", { name: "Sardine 18/11/2025 An old message" })
    ).toBeVisible();
  });

  test("should not be able to import message if not mailbox admin", async ({
    page,
    browserName,
  }) => {
    const email = `user.e2e.${browserName}@example.local`;
    await page.waitForLoadState("networkidle");

    // Go to the shared mailbox where the user only has sender rights
    await page.getByRole("button", { name: email }).click();
    await page
      .getByRole("menuitem")
      .filter({ hasText: getMailboxEmail("shared") })
      .click();
    await page.waitForLoadState("networkidle");

    // The "More options" menu mixes mailbox-scoped entries with user-scoped
    // ones (Domain admin, and Notifications when PUSH_ENABLED). Whether it
    // opens at all therefore depends on the user and on the deployment config
    // — asserting the button is disabled would only be testing that this user
    // happens to have no user-scoped entry either. The invariant that must
    // hold in every configuration is narrower: the menu never offers to import
    // into a mailbox the user does not administer.
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    if (await settingsButton.isEnabled()) {
      await settingsButton.click();
      // Let the menu render before asserting an absence below, otherwise the
      // assertion would happily pass against a menu that has not opened yet.
      await expect(page.getByRole("menuitem").first()).toBeVisible();
    }

    await expect(
      page.getByRole("menuitem", { name: "Import messages" })
    ).toHaveCount(0);
  });
});
