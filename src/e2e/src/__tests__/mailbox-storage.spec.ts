import test, { expect } from "@playwright/test";
import { resetDatabase } from "../utils";
import { openMailboxSettingsModal, signInKeycloakIfNeeded } from "../utils-test";

/**
 * Storage tab of the mailbox settings: an admin sees the conversations taking
 * up the most room and can move a selection of them to the trash at once.
 */

const ALPHA = "Inbox thread alpha";
const BETA = "Inbox thread beta";

test.describe("Mailbox storage tab", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.afterAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");
  });

  test("should select every conversation from the selection bar", async ({ page }) => {
    const modal = await openMailboxSettingsModal(page);
    await modal.getByRole("tab", { name: "Storage" }).click();

    const title = modal.getByRole("heading", { name: "Threads using the most storage", level: 3 });
    const selectionBar = modal.locator(".mailbox-settings__storage-selection-bar");
    const rowCheckboxes = modal.locator(".mailbox-settings__storage-item").getByRole("checkbox");
    // Selecting everything starts from one row: no checkbox until then.
    await expect(selectionBar.getByRole("checkbox")).toHaveCount(0);

    await rowCheckboxes.first().check();
    // The indeterminate mark is drawn over the input, inside its label: a
    // click lands on it, which is what `force` reproduces.
    await selectionBar.getByRole("checkbox", { name: "Select all threads" }).click({ force: true });
    const rowCount = await rowCheckboxes.count();
    for (let index = 0; index < rowCount; index += 1) {
      await expect(rowCheckboxes.nth(index)).toBeChecked();
    }
    await expect(selectionBar.getByRole("status")).toHaveText(`${rowCount} selected threads`);

    // Deselecting everything removes the bar: the focus moves to the title.
    await selectionBar.getByRole("checkbox", { name: "Deselect all threads" }).click({ force: true });
    await expect(selectionBar.getByRole("checkbox")).toHaveCount(0);
    await expect(selectionBar.getByRole("status")).toHaveText("");
    await expect(title).toBeFocused();
  });

  test("should move a selection of conversations to the trash", async ({ page }) => {
    const modal = await openMailboxSettingsModal(page);
    await modal.getByRole("tab", { name: "Storage" }).click();

    const title = modal.getByRole("heading", { name: "Threads using the most storage", level: 3 });
    const selectionBar = modal.locator(".mailbox-settings__storage-selection-bar");
    await expect(title).toBeVisible();
    // The bulk action only shows up once something is selected.
    await expect(selectionBar.getByRole("button", { name: "Move to trash" })).toHaveCount(0);

    // The whole row selects the conversation, not only its checkbox.
    await modal.getByText(ALPHA, { exact: true }).click();
    await expect(modal.getByRole("checkbox", { name: ALPHA })).toBeChecked();
    await modal.getByRole("checkbox", { name: BETA }).check();
    // The count is announced, the title stays put.
    await expect(selectionBar.getByRole("status")).toHaveText("2 selected threads");
    await expect(title).toBeVisible();

    await selectionBar.getByRole("button", { name: "Move to trash" }).click();

    await page.getByText("2 threads have been moved to the trash.").waitFor({ state: "visible" });
    await expect(modal.getByRole("checkbox", { name: ALPHA })).toHaveCount(0);
    await expect(modal.getByRole("checkbox", { name: BETA })).toHaveCount(0);
    // Nothing left selected: the selection bar is empty again.
    await expect(selectionBar.getByRole("status")).toHaveText("");
    await expect(selectionBar.getByRole("button", { name: "Move to trash" })).toHaveCount(0);
  });

  test("should open a conversation in a new tab", async ({ page, context }) => {
    const modal = await openMailboxSettingsModal(page);
    await modal.getByRole("tab", { name: "Storage" }).click();

    const row = modal.locator(".mailbox-settings__storage-item").first();
    const subject = (await row.locator(".mailbox-settings__storage-item-subject").innerText()).trim();
    const [newTab] = await Promise.all([
      context.waitForEvent("page"),
      row.getByRole("link", { name: "Open in new tab" }).click(),
    ]);
    await newTab.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });
    // Opening it changes nothing in the storage tab, nor its selection.
    await expect(modal).toBeVisible();
    await expect(row.getByRole("checkbox")).not.toBeChecked();
  });

  test("should undo a trashing without leaving the settings", async ({ page }) => {
    const modal = await openMailboxSettingsModal(page);
    await modal.getByRole("tab", { name: "Storage" }).click();

    const row = modal.locator(".mailbox-settings__storage-item").first();
    const subject = (await row.locator(".mailbox-settings__storage-item-subject").innerText()).trim();
    await row.getByRole("button", { name: "Move to trash" }).click();
    await expect(modal.getByRole("checkbox", { name: subject })).toHaveCount(0);
    // The focus does not fall back to the page: it lands on the next row.
    await expect(modal.locator(".mailbox-settings__storage-item").first().getByRole("checkbox")).toBeFocused();

    // The toast sits above the modal: its Undo is reachable with it open.
    await page.getByRole("button", { name: "Undo" }).click();
    await expect(modal).toBeVisible();
    await expect(modal.getByRole("checkbox", { name: subject })).toBeVisible();
  });
});
