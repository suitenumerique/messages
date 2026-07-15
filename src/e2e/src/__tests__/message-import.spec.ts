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
    // worker whose progress the imports grid polls — well over the default 30s
    // budget.
    test.setTimeout(120_000);
    await page.waitForLoadState("networkidle");

    // Go the import mailbox
    await page.getByRole("button", { name: getMailboxEmail('user', browserName) }).click();
    await page.getByRole("menuitem").filter({ hasText: getMailboxEmail('import') }).click();
    await page.waitForLoadState("networkidle");

    // As the database is fresh, there should be no threads and the Import
    // messages shortcut should be visible. It is a button opening the settings
    // modal — the importer no longer has a page (nor a modal) of its own.
    await expect(page.getByText("No threads")).toBeVisible();
    await expect(page.getByRole("button", { name: "Import messages" })).toBeVisible();

    // The importer lives in the mailbox settings modal, on the Imports tab; the
    // header menu entry opens it directly on the "new import" sub-view.
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();

    const menuItem = page.getByRole("menuitem", { name: "Import messages" });
    await menuItem.click();

    const settingsModal = page.getByRole("dialog", { name: "Settings" });
    await expect(settingsModal).toBeVisible();
    await expect(settingsModal.getByText("Start a new import")).toBeVisible();

    const fileInput = settingsModal.locator(
      'input[type="file"][name="archive_file"]'
    );

    // Import a wrong file type should show an error: the upload is presigned by
    // the backend, which only signs the archive MIME types, so a PNG never even
    // reaches object storage.
    const importButton = settingsModal.getByRole("button", {
      name: "Import",
      exact: true,
    });
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
    // POST /mailboxes/{id}/imports/ creates the run.
    await importRunPromise;

    // Creating the run hands off to the imports list: the form is replaced by
    // the grid, which tracks the worker server-side.
    await expect(
      page.getByText(
        "Import started. You can close this window — it will keep running in the background."
      )
    ).toBeVisible();

    const importsGrid = settingsModal.locator(".admin-data-grid");
    await expect(importsGrid.getByText("EML")).toBeVisible();

    // Completion is asserted on the UI rather than on a polling response: the
    // grid stops polling on the first terminal status, so racing that single
    // response would be flaky. The archive holds one message, and a run that
    // settled with failures would read "1 failed" next to the count.
    await expect(importsGrid.getByText("1 message imported")).toBeVisible({
      timeout: 60_000,
    });
    await expect(importsGrid.getByText("failed")).toHaveCount(0);

    // Escape does not dismiss the settings modal, so use its close control. The
    // tab layout renders one per pane (sidebar and content); either calls
    // onClose, so take whichever the current layout actually shows.
    await settingsModal
      .getByRole("button", { name: "close", exact: true })
      .filter({ visible: true })
      .first()
      .click();
    await settingsModal.waitFor({ state: "hidden" });

    // Messages are delivered by a Celery worker, so nothing on the wire marks
    // the thread list stale: the mailbox poll picks the new unread count up
    // within 30s and invalidates it from there. Force that round rather than
    // idling through it.
    await page.getByRole("button", { name: "Refresh" }).click();

    // Then expect the new message to be visible in the thread list
    await expect(
      page.getByRole("option", { name: "Sardine 18/11/2025 An old message" })
    ).toBeVisible({ timeout: 15_000 });
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
