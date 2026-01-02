import test, { expect } from "@playwright/test";
import { getMailboxEmail, resetDatabase } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";
import path from "path";
import { FIXTURES_PATH } from "../constants";

test.describe("Send Message", () => {

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test("should send a message then receive it", async ({ page, browserName }) => {
    await page.waitForLoadState("networkidle");

    const newMessageButton = page.getByRole("link", { name: "New message" });
    await newMessageButton.click();

    await page.waitForURL("/mailbox/*/new");

    const draftBoxLink = page.getByRole("link", { name: "Drafts" });
    let initialDraftCount = "0";
    if (await draftBoxLink.locator("span.mailbox__item-counter").isVisible()) {
      initialDraftCount = (await draftBoxLink.locator("span.mailbox__item-counter").textContent()) ?? "0";
    }

    const formHeading = page.getByRole("heading", { name: "New message" });
    await formHeading.waitFor({ state: "visible" });
    await page.getByRole("combobox", { name: "To" }).fill(getMailboxEmail('shared'));
    await page.getByRole("textbox", { name: "Subject" }).fill("Hello everyone!");
    await page.locator(".ProseMirror").pressSequentially("# E2E testing\n\nThis is a test message");
    const fileChooserPromise = page.waitForEvent("filechooser");
    await page.getByRole("button", { name: "Add attachments" }).click();
    const fileChooser = await fileChooserPromise;
    await fileChooser.setFiles(path.join(FIXTURES_PATH, "attachment.png"));

    await page.getByText("Draft saved").waitFor({ state: "visible" });

    // The number of drafts should be incremented by 1
    await draftBoxLink.locator("span.mailbox__item-counter", { hasText: (parseInt(initialDraftCount!) + 1).toString() }).waitFor({ state: "visible" });

    await page.getByRole("button", { name: "Send" }).click();
    await page.getByText("Sending message...").waitFor({ state: "visible" });
    await page.getByText("Message sent successfully").waitFor({ state: "visible" });

    // Once message is sent, the number of drafts should be decremented by 1
    if (initialDraftCount !== "0") {
      await draftBoxLink.locator("span.mailbox__item-counter", { hasText: initialDraftCount! }).waitFor({ state: "visible" });
    } else {
      await draftBoxLink.locator("span.mailbox__item-counter").waitFor({ state: "hidden" });
    }

    // Go to the sentbox and check if the message is there
    await page.getByRole("link", { name: "Sent" }).click();
    const threadItem = page.getByRole("link", { name: "Hello everyone!" }).first();
    await expect(threadItem).toBeVisible();
    expect(await threadItem.textContent()).toMatch(new RegExp(`User E2E ${browserName}`, "i"));

    // Go the shared mailbox and check if the message is there
    await page.getByRole("button", { name: getMailboxEmail('user', browserName) }).click();
    await page.getByRole("menuitem", { name: getMailboxEmail('shared') }).click();
    await page.waitForLoadState("networkidle");

    await page.getByRole("link", { name: "Inbox" }).click();

    const messageItem = page.getByRole("link", { name: "Hello everyone!" }).first();
    await expect(messageItem).toBeVisible();
    expect(await messageItem.textContent()).toMatch(new RegExp(`User E2E ${browserName}`, "i"));

    // Open the message and check its content
    await messageItem.click();
    await page.getByRole("heading", { name: "Hello everyone!", level: 2 }).waitFor({ state: "visible" });
    const threadList = page.locator('.thread-view__messages-list');
    const senderItem = threadList.getByRole("button", { name: getMailboxEmail('user', browserName) });
    await expect(senderItem).toBeVisible();
    const recipientItem = threadList.getByRole("button", { name: getMailboxEmail('shared') });
    await expect(recipientItem).toBeVisible();
    const iframeContent = page.locator('iframe').contentFrame();
    await iframeContent.getByRole('heading', { name: 'E2E testing' }).waitFor({ state: "visible" });
    const messageBodyText = iframeContent.getByText("This is a test message");
    await expect(messageBodyText).toBeVisible();
    await expect(page.getByText("1 attachment")).toBeVisible();
    await expect(page.getByText("attachment.png")).toBeVisible();
  });
});
