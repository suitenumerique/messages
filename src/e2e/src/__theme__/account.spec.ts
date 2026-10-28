import { expect, test } from '@playwright/test';

const ACCOUNT_URL = `${process.env.KEYCLOAK_BASE_URL}/realms/messages/account/`;

const MENU_ACCOUNT = 'Account and sign-in';
const MENU_PERSONAL = 'Personal information';

test.describe('Keycloak lst-dsfr account console', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(ACCOUNT_URL);
    await page.locator('#username').fill('user2@example.local');
    await page.locator('#password').fill('user2');
    await page.locator('#kc-login').click();
    await expect(page.getByRole('link', { name: MENU_PERSONAL })).toBeVisible();
  });

  test('shows the profile fields and the language select', async ({ page }) => {
    await expect(page.locator('input[name="firstName"]')).toBeVisible();
    await expect(page.locator('input[name="lastName"]')).toBeVisible();
    await expect(page.locator('input[type="email"]')).toHaveValue('user2@example.local');
    await expect(page.locator('select[name="locale"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Other services' }).first()).toBeVisible();
  });

  test('shows the password block and the second-factor section', async ({ page }) => {
    await page.getByRole('link', { name: MENU_ACCOUNT }).click();
    await expect(page).toHaveURL(/\/connection-and-account/);
    await expect(
      page.getByRole('button', { name: 'Change my password' }),
    ).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Two-factor authentication' }),
    ).toBeVisible();
    const help = page.getByRole('link', { name: /Help about two-factor/ });
    await expect(help).toHaveAttribute('href', 'https://support.example.local/2fa');
    await expect(help).toHaveAttribute('target', '_blank');
    await expect(
      page.locator('#credential-list').or(
        page.getByText('You have not set up two-factor'),
      ),
    ).toBeVisible();
  });

  test('opens the second-factor choice page', async ({ page }) => {
    await page.getByRole('link', { name: MENU_ACCOUNT }).click();
    await page
      .getByRole('button', {
        name: /Set up two-factor authentication|Add another verification method/,
      })
      .click();
    await expect(page).toHaveURL(/\/double-authentication-choice/);
    await expect(page.getByText('One-time code (TOTP)')).toBeVisible();
    await expect(page.getByRole('radio', { name: /Passkey/ })).toBeVisible();
  });

  test('signs out and shows the login page', async ({ page }) => {
    // The DSFR header renders the quick access links twice: desktop and mobile menu.
    await page.locator('[aria-label^="Sign out"]').first().click();
    await expect(page.locator('#kc-form-login')).toBeVisible();
  });
});
