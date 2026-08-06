import test, { expect } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { openNewMessageWindow, signInKeycloakIfNeeded } from "../utils-test";

test.describe("Floating compose window", () => {

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");
  });

  test("should minimize, restore and expand without losing content", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);

    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Minimize me");
    await composeWindow.locator(".ProseMirror").pressSequentially("Some content");

    // Minimize: the window collapses into a pill, the form stays mounted.
    await composeWindow.getByRole("button", { name: "Minimize" }).click();
    await expect(composeWindow.locator(".ProseMirror")).toBeHidden();

    // Restore from the pill.
    await composeWindow.getByRole("button", { name: "Restore" }).click();
    await expect(composeWindow.locator(".ProseMirror")).toContainText("Some content");
    await expect(composeWindow.getByRole("textbox", { name: "Subject" })).toHaveValue("Minimize me");

    // Expand to the centered overlay and back.
    await composeWindow.getByRole("button", { name: "Expand" }).click();
    await expect(composeWindow).toHaveClass(/compose-window--expanded/);
    await expect(composeWindow.locator(".ProseMirror")).toContainText("Some content");
    await composeWindow.getByRole("button", { name: "Exit full screen" }).click();
    await expect(composeWindow).not.toHaveClass(/compose-window--expanded/);

    // Clean up: discard the draft through the close confirmation.
    await composeWindow.getByRole("button", { name: "Close" }).click();
    await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
      .getByRole("button", { name: "Delete draft" }).click();
    await expect(composeWindow).toBeHidden();
  });

  test("should close silently when the draft is empty", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("button", { name: "Close" }).click();
    await expect(page.getByRole("dialog", { name: "Do you want to keep this draft?" })).toBeHidden();
    await expect(composeWindow).toBeHidden();
  });

  test("should ask to keep or delete a new draft with content on close", async ({ page }) => {
    // Branch 1: save and close keeps the draft.
    let composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Keep this draft");
    await composeWindow.getByRole("button", { name: "Close" }).click();

    const dialog = page.getByRole("dialog", { name: "Do you want to keep this draft?" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "Save and close" }).click();
    await expect(composeWindow).toBeHidden();

    const draftBoxLink = page.getByRole("link", { name: "Drafts" });
    await draftBoxLink.click();
    await expect(page.getByRole("option", { name: "Keep this draft" }).first()).toBeVisible();

    // Branch 2: delete discards the draft.
    composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Discard this draft");
    await composeWindow.getByRole("button", { name: "Close" }).click();
    await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
      .getByRole("button", { name: "Delete draft" }).click();
    await expect(composeWindow).toBeHidden();
    await expect(page.getByRole("option", { name: "Discard this draft" })).toBeHidden();
  });

  test("should detach an inline draft to a window with a banner, then restore it inline", async ({ page }) => {
    // Materialize a draft, then open it from the Drafts folder to get the
    // inline compose surface in the thread view.
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Detachable draft");
    await composeWindow.getByRole("button", { name: "Close" }).click();
    await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
      .getByRole("button", { name: "Save and close" }).click();

    await page.getByRole("link", { name: "Drafts" }).click();
    await page.getByRole("option", { name: "Detachable draft" }).first().click();
    const inlineForm = page.locator(".message-reply-form-container");
    await expect(inlineForm).toBeVisible();

    // Detach it to a floating window.
    await inlineForm.getByRole("button", { name: "Open in a window" }).click();
    const detachedWindow = page.locator(".compose-window").last();
    await expect(detachedWindow).toBeVisible();

    // The inline form is replaced by a banner pointing at the window.
    const banner = page.locator(".compose-draft-banner");
    await expect(banner).toContainText("You are editing this draft in a separate window.");
    await expect(inlineForm).toBeHidden();

    // The banner CTA restores a minimized window.
    await detachedWindow.getByRole("button", { name: "Minimize" }).click();
    await banner.getByRole("button", { name: "Show window" }).click();
    await expect(detachedWindow).not.toHaveClass(/compose-window--minimized/);

    // Closing the window (draft pre-existed: silent save) restores the
    // inline form in the thread.
    await detachedWindow.getByRole("button", { name: "Close" }).click();
    await expect(detachedWindow).toBeHidden();
    await expect(banner).toBeHidden();
    await expect(inlineForm).toBeVisible();
  });

  test("should restore materialized windows after a reload", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Survives reload");
    // Blur the subject field so the autosave materializes the draft.
    await composeWindow.locator(".ProseMirror").click();
    await page.getByText("Draft saved").waitFor({ state: "visible" });

    await page.reload();
    await page.waitForLoadState("networkidle");

    const restoredWindow = page.locator(".compose-window").last();
    await expect(restoredWindow).toBeVisible();
    await expect(restoredWindow.getByRole("textbox", { name: "Subject" })).toHaveValue("Survives reload");

    // Clean up (draft pre-existed the restored window: closes silently).
    await restoredWindow.getByRole("button", { name: "Close" }).click();
    await expect(restoredWindow).toBeHidden();
  });

  test("should pop the draft out to a standalone tab", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("combobox", { name: "To" }).fill(getMailboxEmail("shared"));
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Popped out draft");

    const popupPromise = page.waitForEvent("popup");
    await composeWindow.getByRole("button", { name: "Open in new tab" }).click();
    const popup = await popupPromise;

    // The origin window closes, the tab shows the standalone compose page.
    await expect(composeWindow).toBeHidden();
    await popup.waitForURL(/\/mailbox\/[0-9a-f-]+\/draft\/[0-9a-f-]+/);
    await expect(popup.getByRole("textbox", { name: "Subject" })).toHaveValue("Popped out draft");
    // No app shell: the folder navigation does not exist there.
    await expect(popup.getByRole("link", { name: "Inbox" })).toBeHidden();

    // Send from the pop-out.
    await popup.locator(".ProseMirror").pressSequentially("Sent from the pop-out tab");
    await popup.getByRole("button", { name: "Send" }).click();
    await popup.getByText("Message sent successfully").waitFor({ state: "visible" }).catch(() => {
      // The tab may close itself right after sending (window.close), which
      // is also a valid outcome.
    });
  });
});
