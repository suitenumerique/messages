import test, { expect, type Page } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { openNewMessageWindow, signInKeycloakIfNeeded } from "../utils-test";

/**
 * Toasts are pinned bottom-center — the very spot the mobile compose stack bar
 * occupies — so a lingering "Draft saved" intercepts the clicks meant for the
 * bar. Dismiss whatever is showing before reaching for it.
 */
const dismissToasts = async (page: Page) => {
  const toasts = page.locator(".Toastify__toast");
  for (const toast of await toasts.all()) {
    await toast
      .getByRole("button", { name: "Close" })
      .click({ timeout: 2000 })
      .catch(() => {
        // Auto-dismissed while we were closing the previous one.
      });
  }
  await expect(toasts).toHaveCount(0);
};

test.describe("Floating compose window", () => {

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");
  });

  test("should minimize, restore and detach without losing content", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);

    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Minimize me");
    await composeWindow.locator(".ProseMirror").pressSequentially("Some content");

    // Minimize: the window collapses into a pill, the form stays mounted.
    // `exact` throughout: the window's own title button is named after the
    // subject, so a substring match on a control name is ambiguous as soon as
    // the subject contains it ("Minimize me" vs "Minimize").
    await composeWindow.getByRole("button", { name: "Minimize", exact: true }).click();
    await expect(composeWindow.locator(".ProseMirror")).toBeHidden();

    // Restore from the pill (the control flips to "Open" once minimized).
    await composeWindow.getByRole("button", { name: "Open", exact: true }).click();
    await expect(composeWindow.locator(".ProseMirror")).toContainText("Some content");
    await expect(composeWindow.getByRole("textbox", { name: "Subject" })).toHaveValue("Minimize me");

    // Detach to the centered floating overlay and dock it back.
    await composeWindow.getByRole("button", { name: "Detach" }).click();
    await expect(composeWindow).toHaveClass(/compose-window--floating/);
    await expect(composeWindow.locator(".ProseMirror")).toContainText("Some content");
    await composeWindow.getByRole("button", { name: "Dock" }).click();
    await expect(composeWindow).not.toHaveClass(/compose-window--floating/);

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

  test("should reopen a saved draft directly in a window from the Drafts list", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Resume me from the list");
    await composeWindow.getByRole("button", { name: "Close" }).click();
    await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
      .getByRole("button", { name: "Save and close" }).click();

    await page.getByRole("link", { name: "Drafts" }).click();
    await page.waitForLoadState("networkidle");
    const listUrl = page.url();

    // Clicking a draft-only thread opens the compose window in place,
    // without navigating to a thread view.
    await page.getByRole("option", { name: "Resume me from the list" }).first().click();
    const reopened = page.locator(".compose-window").last();
    await expect(reopened).toBeVisible();
    await expect(reopened.getByRole("textbox", { name: "Subject" })).toHaveValue("Resume me from the list");
    expect(page.url()).toBe(listUrl);

    // Clean up: the draft pre-existed the window, deletion goes through the
    // confirm-less silent close then the Drafts list.
    await reopened.getByRole("button", { name: "Close" }).click();
    await expect(reopened).toBeHidden();
  });

  test("should show a placeholder for a reply draft that resumes in a window", async ({ page }) => {
    // Create a thread by sending a message, then start a reply on it.
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("combobox", { name: "To" }).fill(getMailboxEmail("shared"));
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Thread with a reply draft");
    await composeWindow.locator(".ProseMirror").pressSequentially("Original message");
    await composeWindow.getByRole("button", { name: "Send" }).click();
    await page.getByText("Message sent successfully").waitFor({ state: "visible" });

    await page.getByRole("link", { name: "Sent" }).click();
    await page.getByRole("option", { name: "Thread with a reply draft" }).first().click();
    await page.getByRole("heading", { name: "Thread with a reply draft", level: 2 }).waitFor({ state: "visible" });

    // A live reply session stays inline. Scoped to the message footer: the
    // message header carries its own icon-only "Reply" button.
    await page
      .locator(".thread-message__footer-actions")
      .getByRole("button", { name: "Reply", exact: true })
      .click();
    const inlineForm = page.locator(".message-reply-form-container");
    await expect(inlineForm).toBeVisible();
    await inlineForm.locator(".ProseMirror").pressSequentially("Reply in progress");
    // No "Draft saved" gate here: that toast only fires when the draft is
    // *created*, and typing alone does not trigger a save (it takes a blur or
    // the 30s autosave). Detaching materializes the draft on its own, so the
    // assertions below are what really prove the content survived.

    // Detaching the session moves it to a window; the thread marks the
    // draft position with the "separate window" placeholder.
    await inlineForm.getByRole("button", { name: "Open in a window" }).click();
    const detachedWindow = page.locator(".compose-window").last();
    await expect(detachedWindow).toBeVisible();
    const placeholder = page.locator(".compose-draft-placeholder");
    await expect(placeholder).toContainText("You are editing this draft in a separate window.");
    await expect(inlineForm).toBeHidden();

    // Closing the window leaves the resume placeholder — the inline form
    // does not come back: drafts always reopen in a window.
    await detachedWindow.getByRole("button", { name: "Close" }).click();
    await expect(detachedWindow).toBeHidden();
    await expect(placeholder).toContainText("Continue editing");
    await expect(inlineForm).toBeHidden();

    // The placeholder resumes the draft in a window, content intact. Only its
    // banner action is clickable — the placeholder itself is a plain container.
    await placeholder.getByRole("button", { name: "Continue editing" }).click();
    const resumed = page.locator(".compose-window").last();
    await expect(resumed).toBeVisible();
    await expect(resumed.locator(".ProseMirror")).toContainText("Reply in progress");

    // Clean up: the draft pre-existed the window, closing keeps it silently.
    await resumed.getByRole("button", { name: "Close" }).click();
    await expect(resumed).toBeHidden();

    // On desktop, opening the thread anew auto-opens the draft inline,
    // ready to edit — no placeholder click needed.
    await page.reload();
    await page.getByRole("link", { name: "Sent" }).click();
    await page.getByRole("option", { name: "Thread with a reply draft" }).first().click();
    await expect(inlineForm).toBeVisible();
    await expect(inlineForm.locator(".ProseMirror")).toContainText("Reply in progress");
  });

  test("should show only one expanded window, the most recent on the right", async ({ page }) => {
    const first = await openNewMessageWindow(page);
    await first.getByRole("textbox", { name: "Subject" }).fill("First window");

    const second = await openNewMessageWindow(page);
    await expect(second).not.toHaveClass(/compose-window--minimized/);

    // Opening the second window collapsed the first into a dock tab, and the
    // MRU row keeps the expanded window as the last (rightmost) element.
    const windows = page.locator(".compose-window");
    await expect(windows).toHaveCount(2);
    await expect(windows.first()).toHaveClass(/compose-window--minimized/);
    await expect(windows.first()).toContainText("First window");

    // Restoring the tab expands it in place: it keeps its slot on the left,
    // the previously expanded window collapses where it stands.
    await windows.first().getByRole("button", { name: "Open", exact: true }).click();
    const restored = page.locator(".compose-window").first();
    await expect(restored).toContainText("First window");
    await expect(restored).not.toHaveClass(/compose-window--minimized/);
    await expect(page.locator(".compose-window").last()).toHaveClass(/compose-window--minimized/);

    // Clean up both windows.
    await restored.getByRole("button", { name: "Close" }).click();
    await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
      .getByRole("button", { name: "Delete draft" }).click();
    const remaining = page.locator(".compose-window").last();
    await remaining.getByRole("button", { name: "Open", exact: true }).click();
    await remaining.getByRole("button", { name: "Close", exact: true }).click();
    await expect(page.locator(".compose-window")).toHaveCount(0);
  });

  test("should fold overflowing dock tabs behind a +X dropdown", async ({ page }) => {
    // Desktop cap: 3 visible windows, expanded included. Open 5 windows:
    // the two oldest tabs overflow.
    const first = await openNewMessageWindow(page);
    await first.getByRole("textbox", { name: "Subject" }).fill("Oldest window");
    for (let i = 0; i < 4; i++) {
      const extra = await openNewMessageWindow(page);
      // Every window needs a subject of its own: asking for a new message
      // while a blank untitled one is open focuses that one instead of
      // stacking a second (deduplication is deliberate).
      await extra.getByRole("textbox", { name: "Subject" }).fill(`Window ${i + 2}`);
    }

    await expect(page.locator(".compose-window")).toHaveCount(5);
    await expect(page.locator(".compose-window--overflow")).toHaveCount(2);
    const overflowButton = page.getByRole("button", { name: "2 more compose windows" });
    await expect(overflowButton).toBeVisible();

    // The dropdown restores the hidden window, content intact.
    await overflowButton.click();
    await page.getByRole("menuitem", { name: "Oldest window" }).click();
    const restored = page.locator(".compose-window").last();
    await expect(restored).not.toHaveClass(/compose-window--minimized/);
    await expect(restored.getByRole("textbox", { name: "Subject" })).toHaveValue("Oldest window");
    await expect(page.locator(".compose-window--overflow")).toHaveCount(2);

    // Clean up: they all carry a subject, so each close asks to confirm.
    const discardDialog = page.getByRole("dialog", { name: "Do you want to keep this draft?" });
    await restored.getByRole("button", { name: "Close", exact: true }).click();
    await discardDialog.getByRole("button", { name: "Delete draft" }).click();
    for (let i = 0; i < 4; i++) {
      const tab = page.locator(".compose-window").last();
      await tab.getByRole("button", { name: "Open", exact: true }).click();
      await tab.getByRole("button", { name: "Close", exact: true }).click();
      await discardDialog.getByRole("button", { name: "Delete draft" }).click();
    }
    await expect(page.locator(".compose-window")).toHaveCount(0);
  });

  test("should stack windows behind a mobile bar and an exploded overview", async ({ page }) => {
    // Open two windows on desktop, then shrink to a mobile viewport: the
    // presentation is purely CSS/state so nothing is lost in the resize.
    const first = await openNewMessageWindow(page);
    await first.getByRole("textbox", { name: "Subject" }).fill("Mobile A");
    // The subject reaches the window descriptor asynchronously, and an
    // untitled "new" window is deduplicated: wait for the title to land or
    // the next click just refocuses this window instead of opening one.
    await expect(first.locator(".compose-window__title")).toHaveText("Mobile A");
    const second = await openNewMessageWindow(page);
    await second.getByRole("textbox", { name: "Subject" }).fill("Mobile B");

    await page.setViewportSize({ width: 390, height: 844 });

    // The expanded window becomes a full-screen sheet.
    const sheet = page.locator(".compose-window--sheet");
    await expect(sheet).toBeVisible();
    await expect(sheet.getByRole("textbox", { name: "Subject" })).toHaveValue("Mobile B");

    // Clicking the title minimizes it and reveals the pile bar. The
    // swipe-down gesture that shares this header is native-only, precisely so
    // it does not capture the pointer and swallow this click.
    await sheet.locator(".compose-window__title").click();
    const stack = page.getByRole("button", { name: "2 compose windows" });
    await expect(stack).toBeVisible();

    // The pile opens the exploded overview; picking a card resumes it.
    await dismissToasts(page);
    await stack.click();
    const overview = page.getByRole("dialog", { name: "Compose windows" });
    await expect(overview).toBeVisible();
    await overview.getByRole("button", { name: "Mobile A" }).click();
    await expect(overview).toBeHidden();
    await expect(page.locator(".compose-window--sheet").getByRole("textbox", { name: "Subject" }))
      .toHaveValue("Mobile A");

    // Clean up both drafts through the sheet close flow.
    for (const subject of ["Mobile A", "Mobile B"]) {
      const openSheet = page.locator(".compose-window--sheet");
      await expect(openSheet.getByRole("textbox", { name: "Subject" })).toHaveValue(subject);
      // The sheet slides into place, and its header controls sit under
      // tooltips that mount on hover. A click fired mid-slide, or on a button
      // the pointer has never visited, is swallowed — settle both first.
      await openSheet.evaluate((el) =>
        Promise.all(el.getAnimations({ subtree: true }).map((animation) => animation.finished)),
      );
      const closeButton = openSheet.getByRole("button", { name: "Close" });
      await closeButton.hover();
      await closeButton.click();
      await page.getByRole("dialog", { name: "Do you want to keep this draft?" })
        .getByRole("button", { name: "Delete draft" }).click();
      await expect(openSheet).toBeHidden();
      if (subject === "Mobile A") {
        await dismissToasts(page);
        await page.locator(".compose-mobile-stack").click();
      }
    }
    await expect(page.locator(".compose-window")).toHaveCount(0);
  });

  test("should restore materialized windows after a reload", async ({ page }) => {
    const composeWindow = await openNewMessageWindow(page);
    await composeWindow.getByRole("textbox", { name: "Subject" }).fill("Survives reload");
    // Blur the subject field so the autosave materializes the draft.
    await composeWindow.locator(".ProseMirror").click();
    await page.getByText("Draft saved").waitFor({ state: "visible" });

    // The window list is persisted on a 300ms debounce, and only windows whose
    // draft exists server-side are kept. Reloading before that flush would drop
    // the window for a reason the feature is not responsible for.
    await expect
      .poll(() =>
        page.evaluate(() =>
          JSON.parse(localStorage.getItem("messages:compose-windows:v2") ?? "[]").length,
        ),
      )
      .toBe(1);

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
