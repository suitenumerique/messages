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
    const email = `user.e2e.${browserName}@example.local`;
    await page.waitForLoadState("networkidle");

    // As the database is fresh, there should be no threads and the Import messages button should be visible
    const noThreads = page.getByText("No threads.");
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

    await expect(
      page.getByText("Your messages have been imported successfully!")
    ).toBeVisible();

    const closeButton = page.getByRole("button", {
      name: "Close",
      exact: true,
    });
    await closeButton.click();

    await importModal.waitFor({ state: "hidden" });

    // Then expect the new message to be visible in the thread list
    await expect(
      page.getByRole("link", { name: "Sardine 18/11/2025 An old message" })
    ).toBeVisible();
  });

  test("should not be able to import message if not mailbox admin", async ({
    page,
    browserName,
  }) => {
    const email = `user.e2e.${browserName}@example.local`;
    await page.waitForLoadState("networkidle");

    // Go to the shared mailbox and check if the user has sender rights
    await page.getByRole("button", { name: email }).click();
    await page
      .getByRole("menuitem", { name: getMailboxEmail("shared") })
      .click();
    await page.waitForLoadState("networkidle");

    // As the database is fresh, there should be no threads but the Import messages button should not be visible
    // as the user does not have admin rights to the shared mailbox
    await expect(page.getByText("No threads.")).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Import messages" })
    ).not.toBeVisible();

    // And also in the header settings menu, there should be no entry to import messages
    const header = page.locator(".c__header");
    const settingsButton = header.getByRole("button", { name: "More options" });
    await settingsButton.click();
    await expect(
      page.getByRole("menuitem", { name: "Import messages" })
    ).not.toBeVisible();
  });
});
