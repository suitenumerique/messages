import test, { expect, Page } from "@playwright/test";
import { getMailboxEmail, resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

/**
 * Trash purge: trashed and spam conversations can be permanently deleted from
 * their folder by the roles TRASHBIN_ALLOW_EMPTY allows (mailbox admins by
 * default). The folders announce the automatic deletion after
 * TRASHBIN_CUTOFF_DAYS (set to 30 days in the e2e environment).
 *
 * Deletions above EMPTY_TRASHBIN_SYNC_LIMIT run in a background task: out of
 * reach here, the demo data stays well below it.
 */

const ALPHA = "Inbox thread alpha";
const BETA = "Inbox thread beta";
const SHARED = "Shared inbox thread for IM";

type Folder = "Trash" | "Spam";

/**
 * Open a sidebar folder. Going through the inbox first forces the list to
 * refetch, as the folder may already be the current view.
 */
const openFolder = async (page: Page, folder: Folder | "Inbox", expectedSubject: string) => {
  await expect(async () => {
    await page.getByRole("link", { name: "Inbox" }).click();
    if (folder !== "Inbox") await page.getByRole("link", { name: folder }).click();
    await expect(page.getByRole("option", { name: expectedSubject }).first()).toBeVisible({ timeout: 2000 });
  }).toPass({ timeout: 30000 });
};

const openThread = async (page: Page, subject: string) => {
  await page.getByRole("option", { name: subject }).first().click();
  await page.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });
};

/** The thread view toolbar, as opposed to the bulk actions of the list header. */
const threadActions = (page: Page) => page.locator(".thread-action-bar__container");

/** Move an inbox conversation to `folder` from its thread view. */
const sendToFolder = async (page: Page, subject: string, folder: Folder) => {
  await openFolder(page, "Inbox", subject);
  await openThread(page, subject);
  if (folder === "Trash") {
    await threadActions(page).getByRole("button", { name: "Move to trash" }).click();
    await page.getByText("The thread has been moved to the trash.").waitFor({ state: "visible" });
  } else {
    await threadActions(page).getByRole("button", { name: "Report as spam" }).click();
    await page.getByText(/has been reported as spam/).waitFor({ state: "visible" });
  }
};

const trashbinBanner = (page: Page) => page.getByRole("region", { name: "Announcement" });

const confirmDeletion = async (page: Page) => {
  await page.getByRole("dialog").getByRole("button", { name: "Delete" }).click();
};

/** The user is only a sender on the shared mailbox: open it by URL. */
const openSharedMailbox = async (page: Page) => {
  const response = await page.request.get("/api/v1.0/mailboxes/");
  expect(response.ok()).toBeTruthy();
  const mailboxes: { id: string; email: string }[] = await response.json();
  const sharedMailbox = mailboxes.find(({ email }) => email === getMailboxEmail("shared"));
  expect(sharedMailbox).toBeDefined();
  await page.goto(`/mailbox/${sharedMailbox!.id}`);
};

const FOLDERS = [
  {
    folder: "Trash",
    retentionNotice: "Messages that have been in the trash for more than 30 days are automatically and permanently deleted.",
    emptyLabel: "Empty trash",
    emptiedToast: "Trash emptied.",
    // What a member who cannot purge is left with.
    undoLabel: "Restore",
  },
  {
    folder: "Spam",
    retentionNotice: "Messages that have been in spam for more than 30 days are automatically and permanently deleted.",
    emptyLabel: "Empty spam",
    emptiedToast: "Spam emptied.",
    undoLabel: "Remove spam report",
  },
] as const;

for (const { folder, retentionNotice, emptyLabel, emptiedToast, undoLabel } of FOLDERS) {
  test.describe(`${folder} purge`, () => {
    test.describe.configure({ mode: "serial" });

    test.beforeAll(async () => {
      await resetDatabase();
    });

    test.beforeEach(async ({ page, browserName }) => {
      await signInKeycloakIfNeeded({
        page,
        username: `user.e2e.${browserName}`,
      });
      await page.waitForLoadState("networkidle");
    });

    test("should permanently delete a single conversation", async ({ page }) => {
      await sendToFolder(page, ALPHA, folder);
      await sendToFolder(page, BETA, folder);

      await openFolder(page, folder, ALPHA);
      await openThread(page, ALPHA);
      // Both folders already belong to the trashbin: none offers to trash.
      await expect(threadActions(page).getByRole("button", { name: "Move to trash" })).toHaveCount(0);
      await threadActions(page).getByRole("button", { name: "Delete permanently" }).click();
      // The open conversation is named as such, not as a selection.
      await expect(page.getByRole("dialog")).toContainText("You are about to permanently delete this thread.");
      await confirmDeletion(page);

      await page.getByText("The thread has been permanently deleted.").waitFor({ state: "visible" });
      await expect(page.getByRole("option", { name: ALPHA })).toHaveCount(0);
      await expect(page.getByRole("option", { name: BETA }).first()).toBeVisible();
    });

    test("should announce the retention policy and empty the folder", async ({ page }) => {
      await openFolder(page, folder, BETA);

      const banner = trashbinBanner(page);
      await expect(banner).toContainText(retentionNotice);

      // Backing out of the confirmation deletes nothing.
      await banner.getByRole("button", { name: emptyLabel }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
      await expect(page.getByRole("dialog")).toHaveCount(0);
      await expect(page.getByRole("option", { name: BETA }).first()).toBeVisible();

      await banner.getByRole("button", { name: emptyLabel }).click();
      await confirmDeletion(page);

      await page.getByText(emptiedToast).waitFor({ state: "visible" });
      await expect(page.getByRole("option", { name: BETA })).toHaveCount(0);
      await expect(page.getByRole("option", { name: ALPHA })).toHaveCount(0);
      // Nothing left to empty: only the retention notice remains.
      await expect(banner.getByRole("button", { name: emptyLabel })).toHaveCount(0);
      await expect(banner).toContainText(retentionNotice);
    });

    test("should not let a non-admin member purge a shared mailbox", async ({ page }) => {
      // TRASHBIN_ALLOW_EMPTY keeps purging to mailbox admins: a sender can
      // still move a conversation to the folder, and take it back out.
      await openSharedMailbox(page);
      await sendToFolder(page, SHARED, folder);

      await openFolder(page, folder, SHARED);
      const banner = trashbinBanner(page);
      await expect(banner).toContainText(retentionNotice);
      await expect(banner.getByRole("button", { name: emptyLabel })).toHaveCount(0);

      await openThread(page, SHARED);
      await expect(threadActions(page).getByRole("button", { name: undoLabel })).toBeVisible();
      await expect(threadActions(page).getByRole("button", { name: "Delete permanently" })).toHaveCount(0);
      await expect(threadActions(page).getByRole("button", { name: "Move to trash" })).toHaveCount(0);
    });
  });
}
