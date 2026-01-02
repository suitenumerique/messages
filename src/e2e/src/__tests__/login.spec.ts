import { test, expect } from '@playwright/test';
import { signInKeycloakIfNeeded } from '../utils-test';
import { getStorageStatePath } from '../utils';

test.describe('Authentication with empty storage state', () => {
  test.use({ storageState: { cookies: [], origins: [] } });
  test('should authenticate', async ({ page, browserName }) => {
    const username = `user.e2e.${browserName}`;
    await signInKeycloakIfNeeded({ page, username });
  });
});

test.describe('Authentication with existing storage state', () => {
  test('should authenticate', async ({ page, browserName }) => {
    const username = `user.e2e.${browserName}`;
    await signInKeycloakIfNeeded({ page, username });
  });
});

