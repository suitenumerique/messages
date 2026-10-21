import { expect, Page, test } from '@playwright/test';

const ACCOUNT_URL = `${process.env.KEYCLOAK_BASE_URL}/realms/messages/account/`;

// The authorization endpoint reads `ui_locales`, so the locale goes on the login URL.
const openLoginPage = async (page: Page, locale: 'fr' | 'en') => {
  await page.goto(ACCOUNT_URL);
  await page.waitForURL(/\/protocol\/openid-connect\/auth\?/);
  await page.goto(`${page.url()}&ui_locales=${locale}`);
  await expect(page.locator('#kc-form-login')).toBeVisible();
};

test.describe('Keycloak lst-dsfr login page', () => {
  test('shows the DSFR header and the French title', async ({ page }) => {
    await openLoginPage(page, 'fr');
    await expect(page.locator('header.fr-header')).toBeVisible();
    await expect(page.locator('h1#kc-page-title')).toHaveText('Accéder au compte');
  });

  test('shows the English title', async ({ page }) => {
    await openLoginPage(page, 'en');
    await expect(page.locator('h1#kc-page-title')).toHaveText('Access your account');
  });

  // The dev realm sets rememberMe to true, resetPasswordAllowed and registrationAllowed to false.
  test('shows remember me but no forgot-password or registration', async ({ page }) => {
    await openLoginPage(page, 'en');
    await expect(page.locator('input#rememberMe[name="rememberMe"]')).toBeAttached();
    await expect(page.getByText('Remember me')).toBeVisible();
    await expect(page.locator('#kc-forgot-password')).toHaveCount(0);
    await expect(page.locator('#kc-registration')).toHaveCount(0);
  });

  test('shows a field error on a wrong password', async ({ page }) => {
    await openLoginPage(page, 'en');
    await page.locator('#username').fill('user1@example.local');
    await page.locator('#password').fill('wrong-password');
    await page.locator('#kc-login').click();
    await expect(page.locator('#input-error')).toContainText('Invalid username or password');
  });

  test('lands on the account console with a valid password', async ({ page }) => {
    await openLoginPage(page, 'fr');
    await page.locator('#username').fill('user1@example.local');
    await page.locator('#password').fill('user1');
    await page.locator('#kc-login').click();
    await expect(page).toHaveURL(/\/realms\/messages\/account/);
    await expect(
      page.getByRole('link', { name: 'Personal information' }),
    ).toBeVisible();
  });
});
