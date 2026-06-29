import test, { expect, type Page } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";
import path from "path";
import { FIXTURES_PATH } from "../constants";

/**
 * E2E coverage for the in-app attachment preview modal.
 *
 * The viewer is the kit's ``FilePreview`` component (rendered once at the
 * MainLayout level), so we anchor on its stable internals:
 *  - ``[data-testid="file-preview"]`` — the modal root.
 *  - ``.image-viewer__image`` — the rendered image (``alt`` = file name).
 *  - ``.file-preview__{next,previous}-button`` — file navigation.
 *  - the header ``info_outline`` button toggles the (off-screen by default)
 *    sidebar; it must be opened before its actions can be clicked.
 * Everything inside ``.attachment-preview-sidebar*`` is our own component.
 */
test.describe("Attachment preview", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  /**
   * Compose and send a message carrying the given fixture files, then open
   * the resulting thread from the Sent box. Returns once the thread view is
   * displayed. Reading from Sent avoids waiting for async (Celery) delivery.
   */
  async function sendMessageWithAttachments(
    page: Page,
    subject: string,
    fixtures: string[],
  ): Promise<void> {
    await page.waitForLoadState("networkidle");
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail("shared"));
    await page.getByRole("textbox", { name: "Subject" }).fill(subject);
    await page.locator(".ProseMirror").pressSequentially("Please find the files attached.");

    const fileChooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Add attachments" }).click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(fixtures.map((f) => path.join(FIXTURES_PATH, f)));

    // Wait for the uploads to *complete* before sending. An in-progress upload
    // already renders the file name (so getByText can't gate this), but only a
    // finished attachment exposes a "Preview <name>" trigger. Sending too early
    // drops the not-yet-persisted blobs and the message arrives with no PJ.
    await expect(page.getByRole("button", { name: /^Preview / })).toHaveCount(
      fixtures.length,
      { timeout: 15000 },
    );

    await page.getByText("Draft saved").waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText("Message sent successfully").waitFor({ state: "visible" });

    await page.getByRole("link", { name: "Sent" }).click();
    await page.getByRole("option", { name: subject }).first().click();
    await page.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });
  }

  test("previews an image attachment with its provenance and returns to the conversation", async ({
    page,
  }) => {
    await sendMessageWithAttachments(page, "Preview image e2e", ["attachment.png"]);

    // Open the viewer on the attachment from the thread's attachment list.
    await expect(page.getByText("attachment.png")).toBeVisible();
    await page.getByRole("button", { name: "Preview attachment.png" }).click();

    const modal = page.getByTestId("file-preview");
    await expect(modal).toBeVisible();

    // The image viewer renders the bytes streamed from the preview endpoint.
    // It only gets a non-zero box once the image has actually loaded.
    await expect(modal.locator("img.image-viewer__image")).toBeVisible({ timeout: 10000 });

    // The provenance sidebar starts collapsed (off-screen): open it via the
    // header info button before asserting/clicking its content.
    await modal.locator('button:has(.material-icons:text-is("info_outline"))').click();
    const sidebar = page.locator(".attachment-preview-sidebar");
    await expect(sidebar.getByText("Provenance")).toBeVisible();
    await expect(page.locator(".attachment-preview-sidebar__file-name")).toHaveText("attachment.png");
    // The provenance must point back at the source message we just sent.
    await expect(sidebar.getByText("Preview image e2e")).toBeVisible();

    // "Show in conversation" closes the viewer and brings the user back to
    // the thread where the attachment lives.
    await page.getByRole("button", { name: "Show in conversation" }).click();
    await expect(modal).toBeHidden();
    await expect(page.getByRole("heading", { name: "Preview image e2e", level: 2 })).toBeVisible();
  });

  test("navigates between multiple attachments of a thread", async ({ page }) => {
    await sendMessageWithAttachments(page, "Preview navigation e2e", [
      "attachment.png",
      "sample.txt",
    ]);

    // The viewer's file order mirrors the attachment list order, so read the
    // names straight from the DOM rather than assuming an upload order. The
    // list renders a beat after the thread heading (it depends on the messages
    // query), so wait for both items before reading — allTextContents() has no
    // auto-waiting of its own.
    const nameLocator = page.locator(".thread-attachment-list__body .attachment-item-name");
    await expect(nameLocator).toHaveCount(2);
    const names = await nameLocator.allTextContents();
    const [first, second] = names;

    await page.getByRole("button", { name: `Preview ${first}` }).click();
    const modal = page.getByTestId("file-preview");
    await expect(modal).toBeVisible();
    // The sidebar file name reflects which file is currently shown (textContent
    // is readable even while the sidebar is collapsed off-screen).
    await expect(page.locator(".attachment-preview-sidebar__file-name")).toHaveText(first);

    await page.locator(".file-preview__next-button button").click();
    await expect(page.locator(".attachment-preview-sidebar__file-name")).toHaveText(second);

    await page.locator(".file-preview__previous-button button").click();
    await expect(page.locator(".attachment-preview-sidebar__file-name")).toHaveText(first);

    // The header close button dismisses the modal.
    await page.locator(".file-preview__header__content__left button").first().click();
    await expect(modal).toBeHidden();
  });
});
