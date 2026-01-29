
import { expect, Page } from "@playwright/test";
import { AUTHENTICATION_URL } from "./constants";
import { getStorageStatePath } from "./utils";

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

    expect(proConnectButton).not.toBeVisible();
    const mailboxName = await page.getByRole('button', { name: email });
    expect(mailboxName).toBeVisible();

    await page.context().storageState({ path: storageStatePath });
};
