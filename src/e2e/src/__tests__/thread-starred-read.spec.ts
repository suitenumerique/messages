import test, { expect } from "@playwright/test";
import { resetDatabase } from "../utils";
import { signInKeycloakIfNeeded, inboxFolderLink } from "../utils-test";

test.describe("Thread starred", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({
      page,
      username: `user.e2e.${browserName}`,
    });
  });

  test("should star a thread and display the starred marker", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox where demo threads exist
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    // Open the first thread
    await page
      .getByRole("option", { name: "Test message with delivery failure" })
      .first()
      .click();
    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Verify the thread is not yet starred
    const starButton = page.getByRole("button", {
      name: "Star this thread",
    });
    await expect(starButton).toBeVisible();

    // Star the thread
    await starButton.click();

    // Verify the button updates to "Unstar"
    await expect(
      page.getByRole("button", { name: "Unstar this thread" }),
    ).toBeVisible();

    // Verify the star badge appears on the thread item in the list
    const threadList = page.locator(".thread-panel__threads_list");
    await expect(threadList.getByLabel("Starred").first()).toBeVisible();
  });

  test("should unstar a previously starred thread", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    // Open the thread (starred from previous test)
    await page
      .getByRole("option", { name: "Test message with delivery failure" })
      .first()
      .click();
    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Verify it's currently starred
    const unstarButton = page.getByRole("button", {
      name: "Unstar this thread",
    });
    await expect(unstarButton).toBeVisible();

    // Unstar the thread
    await unstarButton.click();

    // Verify the button reverts to "Star this thread"
    await expect(
      page.getByRole("button", { name: "Star this thread" }),
    ).toBeVisible();

    // Verify the star badge disappears from the thread list
    const threadList = page.locator(".thread-panel__threads_list");
    await expect(threadList.getByLabel("Starred")).not.toBeVisible();
  });
});

test.describe("Thread read / unread", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({
      page,
      username: `user.e2e.${browserName}`,
    });
  });

  test("should mark a thread as unread from thread view", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to inbox where received threads exist
    await inboxFolderLink(page).click();
    await page.waitForLoadState("networkidle");

    // Open the thread (the IntersectionObserver auto-marks messages as read)
    await page
      .getByRole("option", { name: "Inbox thread alpha" })
      .first()
      .click();
    await page
      .getByRole("heading", { name: "Inbox thread alpha", level: 2 })
      .waitFor({ state: "visible" });

    // Wait for the auto-read mechanism to kick in: the thread becomes
    // read, so the action bar swaps the "Mark as read" button for
    // "Mark as unread".
    const threadActionBar = page.locator(".thread-action-bar");
    const markAsUnreadButton = threadActionBar.getByRole("button", {
      name: "Mark as unread",
    });
    await expect(markAsUnreadButton).toBeVisible();

    // Click "Mark as unread" — this also triggers unselectThread
    await markAsUnreadButton.click();

    // After marking as unread, the thread is deselected and we're back at the list
    // Verify thread item shows unread indicator
    const unreadThread = page.locator('[data-unread="true"]', {
      hasText: "Inbox thread alpha",
    });
    await expect(unreadThread).toBeVisible();
  });

  test("should keep thread visible after marking as read while unread filter is active", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to inbox
    await inboxFolderLink(page).click();
    await page.waitForLoadState("networkidle");

    // Apply the unread filter first
    await page.getByRole("button", { name: "Filter threads" }).click();
    await page.waitForLoadState("networkidle");

    // Both threads should be visible (both unread)
    await expect(
      page.getByRole("option", { name: "Inbox thread alpha" }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "Inbox thread beta" }).first(),
    ).toBeVisible();

    // Open a thread — the IntersectionObserver auto-marks it as read
    await page
      .getByRole("option", { name: "Inbox thread alpha" })
      .first()
      .click();
    await page
      .getByRole("heading", { name: "Inbox thread alpha", level: 2 })
      .waitFor({ state: "visible" });
    await page.waitForLoadState("networkidle");

    // Close the thread to go back to the list
    await page.getByRole("button", { name: "Close this thread" }).click();

    // The thread should still be visible in the list thanks to thread pinning logic
    // Check @/features/providers/mailbox-cache.ts
    await expect(
      page.getByRole("option", { name: "Inbox thread alpha" }).first(),
    ).toBeVisible();
  });

  test("should filter threads by unread", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Set default filter selection in localStorage and reload so the component picks it up
    // (workaround for getStoredSelectedFilters returning [] on fresh browser contexts)
    await page.evaluate(() => {
      localStorage.setItem(
        "messages_thread-selected-filters",
        JSON.stringify(["has_unread"]),
      );
    });
    await page.reload();
    await page.waitForLoadState("networkidle");

    // Navigate to inbox
    await inboxFolderLink(page).click();
    await page.waitForLoadState("networkidle");

    // Verify both threads are visible initially
    await expect(
      page.getByRole("option", { name: "Inbox thread alpha" }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "Inbox thread beta" }).first(),
    ).toBeVisible();

    // Open the first thread to mark it as read (IntersectionObserver auto-read)
    await page
      .getByRole("option", { name: "Inbox thread alpha" })
      .first()
      .click();
    await page
      .getByRole("heading", { name: "Inbox thread alpha", level: 2 })
      .waitFor({ state: "visible" });

    // Wait for auto-read to propagate: the sidebar reflects the read state
    // once the flag mutation succeeds and the thread list cache is updated.
    await expect(
      page.locator('[data-unread="false"]', {
        hasText: "Inbox thread alpha",
      }),
    ).toBeVisible();

    // Close the thread view to go back to the list
    await page.getByRole("button", { name: "Close this thread" }).click();

    // Click the filter button to apply unread filter (default selected filter)
    await page.getByRole("button", { name: "Filter threads" }).click();
    await page.waitForLoadState("networkidle");

    // The read thread should be filtered out
    await expect(
      page.getByRole("option", { name: "Inbox thread alpha" }),
    ).not.toBeVisible();

    // The unread thread (not opened) should still be visible
    await expect(
      page.getByRole("option", { name: "Inbox thread beta" }).first(),
    ).toBeVisible();

    // Click the filter button again to clear the filter
    await page.getByRole("button", { name: "Filter threads" }).click();
    await page.waitForLoadState("networkidle");

    // Both threads should be visible again
    await expect(
      page.getByRole("option", { name: "Inbox thread alpha" }).first(),
    ).toBeVisible();
    await expect(
      page.getByRole("option", { name: "Inbox thread beta" }).first(),
    ).toBeVisible();
  });
});
