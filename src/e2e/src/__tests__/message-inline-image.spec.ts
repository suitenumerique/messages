import test, { expect } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";
import path from "path";
import { FIXTURES_PATH } from "../constants";

test.describe("Inline Image in Composer", () => {

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test("should insert an inline image via the toolbar button", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to new message form
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // The image upload button should be visible in the toolbar
    const imageButton = page.getByRole("button", { name: "Insert image" });
    await expect(imageButton).toBeVisible();
  });

  test("should upload an inline image and see it as an attachment", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to new message form
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Fill required fields
    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail('shared'));
    await page.getByRole("textbox", { name: "Subject" }).fill("Inline image test");

    // Type some content
    await page.locator(".ProseMirror").pressSequentially("Here is an image:");

    // Click the image upload button in the toolbar
    const imageButton = page.getByRole("button", { name: "Insert image" });
    await imageButton.click();

    // BlockNote shows a file panel with "Upload" tab and "Upload image" button
    const uploadImageButton = page.getByRole("button", { name: "Upload image" });
    await expect(uploadImageButton).toBeVisible({ timeout: 5000 });

    // Click "Upload image" to trigger the file chooser
    const fileChooserPromise = page.waitForEvent("filechooser");
    await uploadImageButton.click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(path.join(FIXTURES_PATH, "attachment.png"));

    // Wait for the image to be uploaded and displayed in the editor
    const inlineImage = page.locator(".ProseMirror img[src*='/api/']");
    await expect(inlineImage).toBeVisible({ timeout: 10000 });

    // The image should also appear in the attachments list
    await expect(page.getByText("attachment.png")).toBeVisible({ timeout: 5000 });
  });

  test("should send a message with an inline image and verify it is received", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to new message form
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Fill the message
    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail('shared'));
    await page.getByRole("textbox", { name: "Subject" }).fill("Message with inline image");
    await page.locator(".ProseMirror").pressSequentially("Check this image below:");

    // Insert inline image via toolbar
    await page.getByRole("button", { name: "Insert image" }).click();

    // Wait for the BlockNote upload panel and click "Upload image"
    const uploadImageButton = page.getByRole("button", { name: "Upload image" });
    await expect(uploadImageButton).toBeVisible({ timeout: 5000 });
    const fileChooserPromise = page.waitForEvent("filechooser");
    await uploadImageButton.click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(path.join(FIXTURES_PATH, "attachment.png"));

    // Wait for image to appear in editor
    await expect(page.locator(".ProseMirror img[src*='/api/']")).toBeVisible({ timeout: 10000 });

    // Wait for draft to save
    await page.getByText("Draft saved").waitFor({ state: "visible" });

    // Send the message
    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText("Sending message...").waitFor({ state: "visible" });
    await page.getByText("Message sent successfully").waitFor({ state: "visible" });

    // Verify the message appears in sentbox
    await page.getByRole("link", { name: "Sent" }).click();
    const sentItem = page.getByRole("link", { name: "Message with inline image" }).first();
    await expect(sentItem).toBeVisible();

    // Open the message and check content
    await sentItem.click();
    await page.getByRole("heading", { name: "Message with inline image", level: 2 }).waitFor({ state: "visible" });

    // The message body should contain the image
    const sentItemContent = page.locator('iframe').contentFrame();
    await expect(sentItemContent.locator('img')).toBeVisible({ timeout: 10000 });
    await expect(sentItemContent.getByText("Check this image below:")).toBeVisible();

    // Switch to shared mailbox and verify the message is received
    // Message delivery is async (Celery task), so we poll until the message appears
    await page.getByTestId('panel-main-left').getByRole("button", { name: getMailboxEmail('user', browserName) }).click();
    await page.getByRole("menuitem", { name: getMailboxEmail('shared') }).click();
    await page.waitForLoadState("networkidle");
    await page.getByRole("link", { name: "Inbox" }).click();

    // Open the message and check content
    const receivedItem = await page.getByRole("link", { name: "Message with inline image" }).first();
    await expect(receivedItem).toBeVisible();
    await receivedItem.click();
    await page.getByRole("heading", { name: "Message with inline image", level: 2 }).waitFor({ state: "visible" });

    // The message body should contain the image
    const iframeContent = page.locator('iframe').contentFrame();
    await expect(iframeContent.locator('img')).toBeVisible({ timeout: 10000 });
    await expect(iframeContent.getByText("Check this image below:")).toBeVisible();
  });

  test("should remove the inline image attachment when image block is deleted", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to new message form
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Fill required fields
    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail('shared'));
    await page.getByRole("textbox", { name: "Subject" }).fill("Delete inline image test");

    // Type content and insert an inline image
    await page.locator(".ProseMirror").pressSequentially("Some text");
    await page.getByRole("button", { name: "Insert image" }).click();

    // Wait for the BlockNote upload panel and click "Upload image"
    const uploadImageButton = page.getByRole("button", { name: "Upload image" });
    await expect(uploadImageButton).toBeVisible({ timeout: 5000 });
    const fileChooserPromise = page.waitForEvent("filechooser");
    await uploadImageButton.click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(path.join(FIXTURES_PATH, "attachment.png"));

    // Wait for image to be uploaded
    const inlineImage = page.locator(".ProseMirror img[src*='/api/']");
    await expect(inlineImage).toBeVisible({ timeout: 10000 });

    // Verify the attachment is listed
    await expect(page.getByText("attachment.png")).toBeVisible({ timeout: 5000 });

    // Select the image block and delete it
    await inlineImage.click();
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");

    // The attachment should be removed after the image block is deleted
    await expect(page.getByText("attachment.png")).toBeHidden({ timeout: 5000 });
  });

  test("should keep regular attachments when inline image is removed", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to new message form
    await page.getByRole("link", { name: "New message" }).click();
    await page.waitForURL("/mailbox/*/new");
    await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

    // Fill required fields
    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail('shared'));
    await page.getByRole("textbox", { name: "Subject" }).fill("Mixed attachments test");

    // Add a regular attachment first
    const fileChooserPromise1 = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Add attachments" }).click();
    const fileChooser1 = await fileChooserPromise1;
    await fileChooser1.setFiles(path.join(FIXTURES_PATH, "sample.txt"));
    await expect(page.getByText("sample.txt")).toBeVisible({ timeout: 5000 });

    // Now add an inline image
    await page.locator(".ProseMirror").pressSequentially("Text with image:");
    await page.getByRole("button", { name: "Insert image" }).click();

    // Wait for the BlockNote upload panel and click "Upload image"
    const uploadImageButton = page.getByRole("button", { name: "Upload image" });
    await expect(uploadImageButton).toBeVisible({ timeout: 5000 });
    const fileChooserPromise2 = page.waitForEvent("filechooser");
    await uploadImageButton.click();
    const fileChooser2 = await fileChooserPromise2;
    await fileChooser2.setFiles(path.join(FIXTURES_PATH, "attachment.png"));

    // Wait for inline image
    await expect(page.locator(".ProseMirror img[src*='/api/']")).toBeVisible({ timeout: 10000 });
    await expect(page.getByText("attachment.png")).toBeVisible({ timeout: 5000 });

    // Delete the inline image
    await page.locator(".ProseMirror img[src*='/api/']").click();
    await page.keyboard.press("Backspace");
    await page.keyboard.press("Backspace");

    // The inline image attachment should be gone but the regular attachment should remain
    await expect(page.getByText("attachment.png")).toBeHidden({ timeout: 5000 });
    await expect(page.getByText("sample.txt")).toBeVisible();
  });
});
