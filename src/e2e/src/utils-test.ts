
import { expect, Locator, Page } from "@playwright/test";
import { AUTHENTICATION_URL } from "./constants";
import { getStorageStatePath } from "./utils";

/**
 * Locate the "Inbox" folder link inside the sidebar mailbox list.
 *
 * Scoped to `nav.mailbox-list` to disambiguate from thread entries whose
 * subject contains "inbox" (e.g. "Shared inbox thread for IM"). Matches the
 * "Inbox" label case-sensitively — the lowercase "inbox" string that
 * appears in textContent from the Material Icons font ligature is skipped
 * by the capital-I match.
 */
export const inboxFolderLink = (page: Page): Locator =>
  page
    .locator("nav.mailbox-list .mailbox__item")
    .filter({ hasText: /Inbox/ })
    .first();

/**
 * Open the mailbox settings modal from the header "More options" menu and return
 * the settings dialog locator. The per-mailbox configuration views (rename,
 * access, signatures, templates, auto-replies, integrations) all live as tabs
 * inside this single modal, so callers select the tab they need on the returned
 * dialog.
 */
export const openMailboxSettingsModal = async (page: Page): Promise<Locator> => {
  const header = page.locator(".c__header");
  await header.getByRole("button", { name: "More options" }).click();
  await page.getByRole("menuitem", { name: "All settings" }).click();
  const modal = page.getByRole("dialog", { name: "Settings" });
  await expect(modal).toBeVisible();
  return modal;
};

export const signInKeycloakIfNeeded = async ({ page, username, navigateTo = "/" }: { page: Page, username: string, navigateTo?: string }) => {
    // Set up response listener BEFORE navigation to avoid race condition
    const meResponsePromise = page.waitForResponse((response) => response.url().includes('/api/v1.0/users/me/') && [200, 401].includes(response.status()));

    // Navigate to the page
    await page.goto(navigateTo);

    // Now await the response
    const meResponse = await meResponsePromise;
    const isAuthenticated = meResponse.status() === 200;

    if (isAuthenticated) return;

    const email = `${username}@example.local`;
    const storageStatePath = getStorageStatePath(username);

    const proConnectButton = page.locator('button.pro-connect-button');
    proConnectButton.click();

    await page.waitForURL(`${AUTHENTICATION_URL}/realms/messages/protocol/openid-connect/auth**`);
    const attemptedUsernameInput = page.locator('input[id="kc-attempted-username"]');
    if (await attemptedUsernameInput.isVisible()) {
        if (await attemptedUsernameInput.inputValue() !== email) {
            const restartLoginButton = page.getByRole('button', { name: 'Restart login' });
            await restartLoginButton.click();
            await page.fill('input[name="username"]', email);
        }
    } else {
        await page.fill('input[name="username"]', email);
    }
    await page.fill('input[name="password"]', 'e2e');
    await page.click('button[type="submit"]');
    await page.waitForURL(`/`, { waitUntil: 'networkidle' });

    await expect(proConnectButton).not.toBeVisible();

    // Confirm the authenticated app shell rendered before snapshotting storage
    // state. The sidebar mailbox selector shows the signed-in address, but it
    // renders as a switcher *button* only for multi-mailbox users; single-mailbox
    // fixtures (e.g. domain_admin) get a static card instead. Match on the
    // address text within the selector rather than a button role, which covers
    // both variants.
    await expect(
        page.locator('.mailbox-selector').getByText(email),
    ).toBeVisible();

    await page.context().storageState({ path: storageStatePath });
};
