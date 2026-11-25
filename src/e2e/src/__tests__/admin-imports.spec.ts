import { test, expect } from "@playwright/test";
import path from "path";
import { FIXTURES_PATH } from "../constants";
import { getMailboxEmail, resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

test.describe("Maildomain imports (Admin)", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `domain_admin.e2e.${browserName}` });
  });

  test.afterEach(async () => {
    await resetDatabase();
  });

  test("should import an archive into a mailbox the domain admin has no access to", async ({ page }) => {
    // The archive is uploaded to object storage, then imported by a Celery
    // worker whose progress the imports grid polls — well over the default 30s
    // budget.
    test.setTimeout(120_000);
    await page.waitForLoadState("networkidle");

    // Navigate to the imports of the domain
    const header = page.locator(".c__header");
    await header.getByRole("button", { name: "More options" }).click();
    await page.getByRole("menuitem", { name: "Domain admin" }).click();
    await page.waitForURL("**/domain");
    await page.getByRole("row").nth(1).click();
    await page.waitForURL("**/domain/*");
    await page.getByRole("link", { name: "Imports" }).click();
    await page.waitForURL("**/domain/*/imports");
    await expect(page.getByText("No import in progress or failed")).toBeVisible();

    // The import mailbox is only shared with its own admins, not with the
    // domain admin: it is picked among every mailbox of the domain.
    const importMailbox = getMailboxEmail("import");
    await page.getByRole("button", { name: "New import" }).click();
    const modal = page.getByRole("dialog", { name: "New import" });
    await modal.getByRole("combobox", { name: "Mailbox to import into" }).fill("import.e2e");
    await page.getByRole("option", { name: importMailbox }).click();

    const fileInput = modal.locator('input[type="file"][name="archive_file"]');
    await fileInput.setInputFiles(path.join(FIXTURES_PATH, "old-message.eml"));
    await expect(modal.getByText("old-message.eml")).toBeVisible();

    // Armed before the click: the archive is small enough that the upload and
    // the import-run creation can both land before a post-click listener would
    // be attached.
    const importRunPromise = page.waitForResponse(
      (response) =>
        /\/api\/v1\.0\/mailboxes\/[^/]+\/imports\/$/.test(response.url()) &&
        response.request().method() === "POST" &&
        response.status() === 202
    );
    await modal.getByRole("button", { name: "Import", exact: true }).click();
    await importRunPromise;

    await expect(
      page.getByText(`Import into ${importMailbox} started. It keeps running in the background.`)
    ).toBeVisible();
    await expect(modal).toBeHidden();

    // A one-message archive can complete before the list first shows it, and
    // the default view only keeps the imports still to watch: list the
    // completed ones too.
    await page.getByRole("button", { name: "Status: In progress, Failed" }).click();
    await page.getByRole("menuitem", { name: "Completed" }).click();
    await page.waitForURL(
      (url) => url.searchParams.get("status")?.split(",").includes("completed") ?? false,
    );

    const importsGrid = page.locator(".admin-data-grid");
    await expect(importsGrid.getByText(importMailbox)).toBeVisible();
    await expect(importsGrid.getByText("EML")).toBeVisible();
    await expect(importsGrid.getByText("1 message imported")).toBeVisible({
      timeout: 60_000,
    });
    await expect(importsGrid.getByText("failed")).toHaveCount(0);
  });
});
