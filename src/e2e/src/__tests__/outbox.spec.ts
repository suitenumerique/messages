import test, { expect } from "@playwright/test";
import { signInKeycloakIfNeeded } from "../utils-test";

test.describe("Delivery failures", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test("should display outbox folder with delivery failed indicator", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // The outbox folder should be visible in the sidebar with "Delivery failed" text
    // because there is a message with failed delivery
    const outboxLink = page.getByRole("link", { name: "Outbox Delivery failed" });
    await expect(outboxLink).toBeVisible();

    // Click on the outbox folder
    await outboxLink.click();
    await page.waitForLoadState("networkidle");

    // The thread with delivery issues should be visible in the list
    const threadItem = page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first();
    await expect(threadItem).toBeVisible();
  });

  test("should show delivery status icons on recipient chips", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox
    const outboxLink = page.getByRole("link", { name: /outbox/i });
    await outboxLink.click();
    await page.waitForLoadState("networkidle");

    // Click on the thread to open it
    const threadItem = page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first();
    await threadItem.click();

    // Wait for thread view to load
    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Check that recipient chips are visible with their emails
    // The failed recipient should have an error icon
    const failedRecipientChip = page.getByRole("button", {
      name: /failed@external\.invalid/i,
    });
    await expect(failedRecipientChip).toBeVisible();

    // The retry recipient should have an update/schedule icon
    const retryRecipientChip = page.getByRole("button", {
      name: /retry@external\.invalid/i,
    });
    await expect(retryRecipientChip).toBeVisible();

    // The sent recipient should be visible (no special icon for delivered)
    const sentRecipientChip = page.getByRole("button", {
      name: /sent@external\.invalid/i,
    });
    await expect(sentRecipientChip).toBeVisible();
  });

  test("should show delivery failure banner with actions", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // The thread should show a banner about delivery failures
    await expect(
      page.getByText("Some recipients have not received this message!")
    ).toBeVisible();

    // The banner should have "Retry" and "Cancel those sendings" buttons
    await expect(page.getByRole("button", { name: "Retry", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "Cancel those sendings", exact: true })).toBeVisible();
  });

  test("should show delivery status tooltip on recipient chip", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Hover over the failed recipient chip to see the tooltip
    const failedRecipientChip = page.getByRole("button", {
      name: /failed@external\.invalid/i,
    });
    await failedRecipientChip.hover();

    // The tooltip should show delivery failure message
    await expect(
      page.getByText("This message has not been delivered.")
    ).toBeVisible();

    // The tooltip should have "Show logs" expandable section
    await expect(page.getByText("Show logs")).toBeVisible();
  });

  test("should cancel all delivery failures", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Verify the failure banner is visible before cancelling
    await expect(
      page.getByText("Some recipients have not received this message!")
    ).toBeVisible();

    // Click the "Cancel those sendings" button
    await page.getByRole("button", { name: "Cancel those sendings" }).click();

    // Wait for the UI to update
    await page.waitForLoadState("networkidle");

    // After cancelling, the failure banner should be gone
    await expect(
      page.getByText("Some recipients have not received this message!")
    ).not.toBeVisible({ timeout: 10000 });
  });

  test("should show delivery logs when expanded", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with delivery failure" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with delivery failure",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Hover over the failed recipient chip to see the tooltip
    const failedRecipientChip = page.getByRole("button", {
      name: /failed@external\.invalid/i,
    });
    await failedRecipientChip.hover();

    // The tooltip should be visible
    await expect(page.getByText("Show logs")).toBeVisible();

    // Click on "Show logs" to expand the delivery logs
    await page.getByText("Show logs").click();

    // The delivery error message should be visible
    await expect(
      page.getByText("Recipient address rejected: Domain not found")
    ).toBeVisible();
  });
});

test.describe("Delivery pending (retry only)", () => {
  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({ page, username: `user.e2e.${browserName}` });
  });

  test("should show pending delivery banner with cancel action", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with pending delivery" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with pending delivery",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // The thread should show a warning banner about pending delivery
    await expect(
      page.getByText("This message has not yet been delivered to all recipients.")
    ).toBeVisible();

    // The banner should have a "Cancel those sendings" button
    await expect(page.getByRole("button", { name: "Cancel those sendings" })).toBeVisible();

    // "Retry" should NOT be visible
    await expect(
      page.getByRole("button", { name: "Retry" })
    ).not.toBeVisible();
  });

  test("should cancel pending delivery", async ({ page }) => {
    await page.waitForLoadState("networkidle");

    // Navigate to outbox and open the thread
    await page.getByRole("link", { name: /outbox/i }).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Test message with pending delivery" })
      .first()
      .click();

    await page
      .getByRole("heading", {
        name: "Test message with pending delivery",
        level: 2,
      })
      .waitFor({ state: "visible" });

    // Verify the pending banner is visible
    await expect(
      page.getByText("This message has not yet been delivered to all recipients.")
    ).toBeVisible();

    // Click the "Cancel" button
    await page.getByRole("button", { name: "Cancel" }).click();

    // Wait for the UI to update
    await page.waitForLoadState("networkidle");

    // After cancelling, the pending banner should be gone
    await expect(
      page.getByText("This message has not yet been delivered to all recipients.")
    ).not.toBeVisible({ timeout: 10000 });
  });
});
