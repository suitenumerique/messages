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
    await importButton.click();
    await expect(errorBanner).not.toBeVisible();

    expect(
      page.getByRole("heading", { name: "Uploading your archive" })
    ).toBeVisible();
    expect(importButton).toBeDisabled();
    expect(await importButton.getAttribute("aria-busy")).toBe("true");

    const uploadCompleteResponse = await page.waitForResponse((response) => {
      return (
        response.url().includes("/api/v1.0/import/file/") &&
        response.status() === 202
      );
    });
    const uploadCompleteData = await uploadCompleteResponse.json();
    const taskId = uploadCompleteData.task_id;

    expect(page.getByText("Importing...")).toBeVisible();

    await page.waitForResponse(async (response) => {
      if (response.url().includes(`/api/v1.0/tasks/${taskId}/`)) {
        const taskData = await response.json();
        return taskData.status === "SUCCESS";
      }
      return false;
    });

    // New completion UI: badge + heading + per-archive stats.
    await expect(page.getByText("Import complete")).toBeVisible();
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

    // The header settings button should be disabled because the user has no
    // admin abilities on the shared mailbox, so no menu option is available.
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await expect(settingsButton).toBeDisabled();
  });
});
