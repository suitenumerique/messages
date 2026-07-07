import test, { expect, Page } from "@playwright/test";
import { getMailboxEmail } from "../utils";
import { signInKeycloakIfNeeded } from "../utils-test";

/**
 * Link preview feature: URLs in a message body are rendered as links, a
 * confirmation modal reveals the real target before opening it, and links
 * are disabled on threads reported as spam.
 */

// Reserved TLD (RFC 2606) so the link can never resolve for real; the
// navigation is served locally through a Playwright route.
const EXTERNAL_URL = "https://external-link.example/promo";

const composeAndSendMessage = async (page: Page, to: string, subject: string, body: string) => {
  await page.getByRole("link", { name: "New message" }).click();
  await page.waitForURL("/mailbox/*/new");
  await page.getByRole("heading", { name: "New message" }).waitFor({ state: "visible" });

  await page.getByRole("combobox", { name: "To" }).fill(to);
  await page.getByRole("textbox", { name: "Subject" }).fill(subject);
  await page.locator(".ProseMirror").pressSequentially(body);
  await page.getByText("Draft saved").waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Send" }).click();
  await page.getByText("Message sent successfully").waitFor({ state: "visible" });
};

test.describe("Message link preview", () => {

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
    await page.waitForLoadState("networkidle");
  });

  test("should render bare URLs as links and ask for confirmation before opening", async ({ page, browserName }) => {
    const subject = `Link preview test ${browserName}`;

    // Serve the external URL locally so the confirmed navigation succeeds
    // in every browser without hitting the network.
    await page.context().route(`${EXTERNAL_URL}**`, (route) =>
      route.fulfill({ contentType: "text/html", body: "<html><body>External page</body></html>" })
    );

    await composeAndSendMessage(
      page,
      getMailboxEmail("user", browserName),
      subject,
      `Check this out ${EXTERNAL_URL} thanks`
    );

    // Open the sent message
    await page.getByRole("link", { name: "Sent" }).click();
    await page.getByRole("option", { name: subject }).first().click();
    await page.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });

    // The bare URL must be rendered as a clickable link in the message body.
    // Scoped to the last message iframe: the latest message is the unfolded one.
    const iframeContent = page.locator("iframe").last().contentFrame();
    const link = iframeContent.locator(`a[href="${EXTERNAL_URL}"]`).first();
    await expect(link).toBeVisible();

    // Clicking the link opens a confirmation modal revealing the target URL
    // instead of navigating directly
    await link.click();
    const modal = page.getByRole("dialog", { name: "External link" });
    await expect(modal).toBeVisible();
    await expect(modal.getByText("You are about to leave this page and be redirected to:")).toBeVisible();
    await expect(modal.getByText(EXTERNAL_URL)).toBeVisible();
    await expect(modal.getByText("Do you want to continue?")).toBeVisible();

    // Cancelling closes the modal without opening the link
    await modal.getByRole("button", { name: "Cancel" }).click();
    await expect(modal).toBeHidden();

    // Confirming opens the link in a new tab
    await link.click();
    await expect(modal).toBeVisible();
    const popupPromise = page.context().waitForEvent("page");
    await modal.getByRole("button", { name: "Yes" }).click();
    const popup = await popupPromise;
    await popup.waitForURL(EXTERNAL_URL);
    await popup.close();
    await expect(modal).toBeHidden();
  });

  test("should disable links on threads reported as spam", async ({ page }) => {
    // A self-sent message is deduplicated and never reaches the inbox, so this
    // thread is seeded by `e2e_demo` as a genuine inbound message (external
    // sender, HTML body carrying EXTERNAL_URL as a bare link).
    const subject = "Spam link inbox thread";

    // Open the received thread from the inbox and report it as spam
    await expect(async () => {
      await page.getByRole("link", { name: "Inbox" }).click();
      await expect(page.getByRole("option", { name: subject }).first()).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 30000 });

    await page.getByRole("option", { name: subject }).first().click();
    await page.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });
    await page.getByRole("button", { name: "Report as spam" }).click();
    await page.getByText(/has been reported as spam/).waitFor({ state: "visible" });

    // Reopen the thread from the Spam folder
    await expect(async () => {
      await page.getByRole("link", { name: "Inbox" }).click();
      await page.getByRole("link", { name: "Spam" }).click();
      await expect(page.getByRole("option", { name: subject }).first()).toBeVisible({ timeout: 2000 });
    }).toPass({ timeout: 30000 });

    await page.getByRole("option", { name: subject }).first().click();
    await page.getByRole("heading", { name: subject, level: 2 }).waitFor({ state: "visible" });

    // Links are inert: no pointer interaction and no confirmation modal
    const iframeContent = page.locator("iframe").last().contentFrame();
    const link = iframeContent.locator(`a[href="${EXTERNAL_URL}"]`).first();
    await expect(link).toBeVisible();
    await expect(link).toHaveCSS("pointer-events", "none");
    await link.click({ force: true });
    await expect(page.getByRole("dialog")).toHaveCount(0);
  });
});
