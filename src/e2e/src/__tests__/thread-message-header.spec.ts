import test, { expect, Locator, Page } from "@playwright/test";
import { signInKeycloakIfNeeded } from "../utils-test";

const SUBJECT = "Inbox thread alpha";

const box = async (locator: Locator) => (await locator.boundingBox())!;

const headerParts = (page: Page) => {
  const message = page.locator(".thread-message").last();
  return {
    actions: message.getByRole("button", { name: "More options" }),
    avatar: message.locator(".thread-message__header-content-avatar"),
    sender: message.locator(".thread-message__sender-chip"),
  };
};

const expectActionBarBesideSender = async (page: Page) => {
  const { actions, sender } = headerParts(page);
  await expect(async () => {
    const actionsBox = await box(actions);
    const senderBox = await box(sender);
    expect(actionsBox.y).toBeLessThan(senderBox.y + senderBox.height);
    expect(actionsBox.y + actionsBox.height).toBeGreaterThan(senderBox.y);
  }).toPass({ timeout: 5000 });
};

const expectActionBarAboveSenderAndAvatar = async (page: Page) => {
  const { actions, avatar, sender } = headerParts(page);
  await expect(async () => {
    const actionsBox = await box(actions);
    const bottom = actionsBox.y + actionsBox.height;
    expect((await box(sender)).y).toBeGreaterThanOrEqual(bottom);
    expect((await box(avatar)).y).toBeGreaterThanOrEqual(bottom);
  }).toPass({ timeout: 5000 });
};

test.describe("Thread message header", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({
      page,
      username: `user.e2e.${browserName}`,
    });
    await page.waitForLoadState("networkidle");
    await page.getByRole("option", { name: SUBJECT }).first().click();
    await page.getByRole("heading", { name: SUBJECT, level: 2 }).waitFor({ state: "visible" });
  });

  test("should give the sender its own line in a narrow thread view", async ({ page }) => {
    await expectActionBarBesideSender(page);

    // The width of the thread view decides, not the one of the viewport: the
    // panel can be resized on a large screen.
    await page.locator(".thread-view__messages-list").evaluate((list: HTMLElement) => {
      list.style.width = "400px";
    });
    await expectActionBarAboveSenderAndAvatar(page);

    await page.locator(".thread-view__messages-list").evaluate((list: HTMLElement) => {
      list.style.width = "";
    });
    await expectActionBarBesideSender(page);
  });

  test("should give the sender its own line on small viewports", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });

    await expectActionBarAboveSenderAndAvatar(page);
  });
});
