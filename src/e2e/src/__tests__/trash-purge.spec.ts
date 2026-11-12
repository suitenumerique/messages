import test, { expect, Page } from "@playwright/test";
import { getMailboxEmail, resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

/**
 * Trash purge: trashed and spam messages can be permanently deleted from their
 * folder by the roles TRASHBIN_ALLOW_EMPTY allows (mailbox admins by default),
 * the other messages of a conversation being kept. The folders announce the
 * automatic deletion after
 * TRASHBIN_CUTOFF_DAYS (set to 30 days in the e2e environment).
 *
 * Deletions above EMPTY_TRASHBIN_SYNC_LIMIT run in a background task: out of
 * reach here, the demo data stays well below it.
 */

const ALPHA = "Inbox thread alpha";
const BETA = "Inbox thread beta";
const SHARED = "Shared inbox thread for IM";
/** Seeded with one live message and one already in the trash. */
const PARTIALLY_TRASHED = "Partially trashed thread";
/** Seeded with one live message and one reported as spam. */
const PARTIALLY_SPAM = "Partially spam thread";
/** Seeded with one live message and one both reported as spam and trashed. */
const TRASHED_SPAM = "Trashed spam thread";

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
    deleteOneNotice: "You are about to permanently delete the trashed messages of this thread.",
    deletedOneToast: "The trashed messages of this thread have been deleted.",
    // What a member who cannot purge is left with.
    undoLabel: "Restore",
  },
  {
    folder: "Spam",
    retentionNotice: "Messages that have been in spam for more than 30 days are automatically and permanently deleted.",
    emptyLabel: "Empty spam",
    emptiedToast: "Spam emptied.",
    deleteOneNotice: "You are about to permanently delete the spam in this thread.",
    deletedOneToast: "The spam in this thread has been deleted.",
    undoLabel: "Remove spam report",
  },
] as const;

for (const { folder, retentionNotice, emptyLabel, emptiedToast, deleteOneNotice, deletedOneToast, undoLabel } of FOLDERS) {
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
      await expect(page.getByRole("dialog")).toContainText(deleteOneNotice);
      await confirmDeletion(page);

      await page.getByText(deletedOneToast).waitFor({ state: "visible" });
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

test.describe("Spam conversation triage", () => {
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

  test("should let a single message move to the trash from the spam folder", async ({ page }) => {
    await openFolder(page, "Spam", PARTIALLY_SPAM);
    await openThread(page, PARTIALLY_SPAM);

    // The spam message, seeded last, is told apart from the other one.
    const spamMessage = page.locator(".thread-message").last();
    await expect(spamMessage.getByLabel("Spam")).toBeVisible();
    await spamMessage.getByLabel("Spam").hover();
    await expect(page.getByRole("tooltip")).toContainText("This message has been reported as spam.");
    await expect(page.locator(".thread-message").first().getByLabel("Spam")).toHaveCount(0);

    await spamMessage.getByRole("button", { name: "More options" }).click();
    await page.getByRole("menuitem", { name: "Move to trash" }).click();
    await page.getByText("The message has been moved to the trash.").waitFor({ state: "visible" });

    // That was its only spam: the conversation leaves the folder at once...
    await expect(page.getByRole("option", { name: PARTIALLY_SPAM })).toHaveCount(0);
    // ... for the inbox with its other message, and the trash with this one.
    await openFolder(page, "Inbox", PARTIALLY_SPAM);
    await openFolder(page, "Trash", PARTIALLY_SPAM);
  });
});

test.describe("Partially trashed conversation purge", () => {
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

  test("should only delete the messages already in the trash", async ({ page }) => {
    const trashedNotice = page.getByText("1 message of this thread is in the trash.");

    // The conversation is both in the inbox and in the trash.
    await openFolder(page, "Inbox", PARTIALLY_TRASHED);
    await openThread(page, PARTIALLY_TRASHED);
    await expect(trashedNotice).toBeVisible();

    await openFolder(page, "Trash", PARTIALLY_TRASHED);
    await openThread(page, PARTIALLY_TRASHED);
    // Trashing it from here would also bin its live message.
    await expect(threadActions(page).getByRole("button", { name: "Move to trash" })).toHaveCount(0);
    await expect(threadActions(page).getByRole("button", { name: "Restore" })).toBeVisible();
    await threadActions(page).getByRole("button", { name: "Delete permanently" }).click();
    await confirmDeletion(page);

    await page.getByText("The trashed messages of this thread have been deleted.").waitFor({ state: "visible" });
    await expect(page.getByRole("option", { name: PARTIALLY_TRASHED })).toHaveCount(0);

    // The live message survives in the inbox, with nothing left in the trash.
    await openFolder(page, "Inbox", PARTIALLY_TRASHED);
    await openThread(page, PARTIALLY_TRASHED);
    await expect(trashedNotice).toHaveCount(0);
  });

  test("should only delete the spam and send the conversation back to the inbox", async ({ page }) => {
    // A single spam message is enough to list the conversation as spam only.
    await page.getByRole("link", { name: "Inbox" }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("option", { name: PARTIALLY_SPAM })).toHaveCount(0);

    await openFolder(page, "Spam", PARTIALLY_SPAM);
    await openThread(page, PARTIALLY_SPAM);
    await threadActions(page).getByRole("button", { name: "Delete permanently" }).click();
    await expect(page.getByRole("dialog")).toContainText("You are about to permanently delete the spam in this thread.");
    await confirmDeletion(page);

    await page.getByText("The spam in this thread has been deleted.").waitFor({ state: "visible" });
    await expect(page.getByRole("option", { name: PARTIALLY_SPAM })).toHaveCount(0);

    // Its other message survives, and brings the conversation back to the inbox.
    await openFolder(page, "Inbox", PARTIALLY_SPAM);
    await openThread(page, PARTIALLY_SPAM);
  });

  test("should keep trashed spam in the trash, and its conversation in the inbox", async ({ page }) => {
    await openFolder(page, "Inbox", TRASHED_SPAM);

    await page.getByRole("link", { name: "Spam" }).click();
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("option", { name: TRASHED_SPAM })).toHaveCount(0);

    await openFolder(page, "Trash", TRASHED_SPAM);
    await openThread(page, TRASHED_SPAM);
    await threadActions(page).getByRole("button", { name: "Delete permanently" }).click();
    await confirmDeletion(page);

    await page.getByText("The trashed messages of this thread have been deleted.").waitFor({ state: "visible" });
    await expect(page.getByRole("option", { name: TRASHED_SPAM })).toHaveCount(0);

    await openFolder(page, "Inbox", TRASHED_SPAM);
    await openThread(page, TRASHED_SPAM);
  });
});
