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

    // The toggle exists on every thread item of the list, and twice in the
    // thread view (the subject heading and its sticky recall), so anchor on
    // the heading's own button. Its accessible name carries the state.
    const starToggle = page.locator(".thread-view__subject__star-button");
    await expect(starToggle).toHaveAccessibleName("Star this thread");

    // Star the thread
    await starToggle.click();
    await expect(starToggle).toHaveAccessibleName("Unstar this thread");

    // The list item reflects it too: its icon is aria-hidden, the state lives
    // on the button that toggles it.
    const threadList = page.locator(".thread-panel__threads_list");
    await expect(
      threadList.getByRole("button", { name: "Unstar this thread" }).first(),
    ).toBeVisible();
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

    // Verify it's currently starred (see the previous test for why the
    // heading's own button is the anchor).
    const starToggle = page.locator(".thread-view__subject__star-button");
    await expect(starToggle).toHaveAccessibleName("Unstar this thread");

    // Unstar the thread
    await starToggle.click();
    await expect(starToggle).toHaveAccessibleName("Star this thread");

    // No list item advertises the starred state anymore.
    const threadList = page.locator(".thread-panel__threads_list");
    await expect(
      threadList.getByRole("button", { name: "Unstar this thread" }),
    ).toHaveCount(0);
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

    // No closing step: on desktop the list and the thread share the screen,
    // and the refactor dropped the "Close this thread" button altogether.

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

    // The list stays on screen next to the thread, so there is nothing to close.

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
