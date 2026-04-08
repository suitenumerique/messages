import test, { expect, Page } from "@playwright/test";
import { resetDatabase, getMailboxEmail } from "../utils";
import { signInKeycloakIfNeeded, inboxFolderLink } from "../utils-test";
import { BrowserName } from "../types";
import { API_URL } from "../constants";

/**
 * Navigate to the shared mailbox and open the IM test thread.
 */
async function navigateToSharedThread(page: Page, browserName: BrowserName) {
  // Wait for page to be fully loaded before interacting with the dropdown
  await page.waitForLoadState("networkidle");

  // Switch from personal mailbox to shared mailbox
  await page
    .getByRole("button", { name: getMailboxEmail("user", browserName) })
    .click();
  await page
    .getByRole("menuitem", { name: getMailboxEmail("shared") })
    .click();
  await page.waitForLoadState("networkidle");

  // Navigate to inbox and open the IM thread
  await inboxFolderLink(page).click();
  await page.waitForLoadState("networkidle");
  await page
    .getByRole("link", { name: "Shared inbox thread for IM" })
    .first()
    .click();
  await page
    .getByRole("heading", {
      name: "Shared inbox thread for IM",
      level: 2,
    })
    .waitFor({ state: "visible" });
  await page.waitForLoadState("networkidle");
}

test.describe("Thread Events (Internal Messages)", () => {
  test.beforeAll(async () => {
    await resetDatabase();
  });

  test.beforeEach(async ({ page, browserName }) => {
    await signInKeycloakIfNeeded({
      page,
      username: `user.e2e.${browserName}`,
    });
  });

  test("should show IM input on shared mailbox thread", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    await expect(
      page.getByPlaceholder("Add internal comment..."),
    ).toBeVisible();

    // Send button should be visible but disabled when input is empty
    const sendButton = page
      .locator(".thread-event-input")
      .getByRole("button", { name: "Send" });
    await expect(sendButton).toBeVisible();
    await expect(sendButton).toBeDisabled();
  });

  test("should not show IM input on personal mailbox inbox thread", async ({
    page,
  }) => {
    await page.waitForLoadState("networkidle");

    await inboxFolderLink(page).click();
    await page.waitForLoadState("networkidle");

    await page
      .getByRole("link", { name: "Inbox thread alpha" })
      .first()
      .click();
    await page
      .getByRole("heading", { name: "Inbox thread alpha", level: 2 })
      .waitFor({ state: "visible" });
    await page.waitForLoadState("networkidle");

    await expect(page.locator(".thread-event-input")).not.toBeVisible();
  });

  test("should send an internal message and display it as a chat bubble", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Hello from E2E test");

    await page
      .locator(".thread-event-input")
      .getByRole("button", { name: "Send" })
      .click();
    await page.waitForLoadState("networkidle");

    // Verify the IM bubble appears with the correct content
    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Hello from E2E test" });
    await expect(bubble).toBeVisible();

    // Verify author bubble shows fullanme
    await expect(bubble.locator(".thread-event__author")).toContainText(
      `User E2E ${browserName}`, { ignoreCase: true }
    );

    // Verify input is cleared
    await expect(imInput).toHaveValue("");
  });

  test("should send an IM by pressing Enter", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Sent with Enter key");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Sent with Enter key" });
    await expect(bubble).toBeVisible();
  });

  test("should condense consecutive messages from same author", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    const imInput = page.getByPlaceholder("Add internal comment...");

    // Send two quick messages
    await imInput.fill("First quick message");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    await imInput.fill("Second quick message");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    // Verify at least one condensed message exists (no repeated header)
    await expect(
      page.locator(".thread-event--condensed").first(),
    ).toBeVisible();
  });

  test("should suggest users when typing @ and insert mention on selection", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    const imInput = page.getByPlaceholder("Add internal comment...");

    // Type @ followed by filter text to trigger the mention popover
    await imInput.pressSequentially("@Mailbox");

    // Wait for the suggestion popover to open
    const popover = page.locator(".suggestion-input__popover--open");
    await expect(popover).toBeVisible();

    // Verify the expected user appears in suggestions
    const browserTitle =
      browserName.charAt(0).toUpperCase() + browserName.slice(1);
    const expectedName = `Mailbox_Admin E2E ${browserTitle}`;
    const suggestionItem = page
      .locator(".suggestion-input__item")
      .filter({ hasText: expectedName });
    await expect(suggestionItem).toBeVisible();

    // Click the suggestion to insert the mention
    await suggestionItem.click();

    // Verify the mention is inserted in the textarea
    await expect(imInput).toHaveValue(new RegExp(`@${expectedName} `));

    // Add text after the mention and send
    await imInput.pressSequentially("can you check this?");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    // Verify the IM bubble contains a rendered mention
    const mentionBubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "can you check this?" });
    await expect(mentionBubble).toBeVisible();
    await expect(
      mentionBubble.locator(".thread-event__mention"),
    ).toContainText(`@${expectedName}`);
  });

  test("should edit an existing IM and show edited badge", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // Send a fresh message to edit
    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Message before edit");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    // Locate the bubble we just sent
    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Message before edit" });
    await expect(bubble).toBeVisible();

    // Hover the bubble to reveal action buttons
    await bubble.locator(".thread-event__bubble").hover();

    // Click the Edit button
    await bubble.getByRole("button", { name: "Edit" }).click();

    // Verify edit mode UI
    await expect(page.locator(".thread-event-input__edit-banner")).toBeVisible();
    await expect(
      page
        .locator(".thread-event-input__edit-banner")
        .getByText("Editing message"),
    ).toBeVisible();

    const saveButton = page
      .locator(".thread-event-input")
      .getByRole("button", { name: "Save" });
    await expect(saveButton).toBeVisible();

    // Verify input is pre-populated with the original message
    await expect(imInput).toHaveValue("Message before edit");

    // Wait so the edit timestamp exceeds the 1s isEdited threshold
    await page.waitForTimeout(1000);

    // Modify content and save
    await imInput.fill("Message after edit");
    await saveButton.click();
    await page.waitForLoadState("networkidle");

    // Verify the updated content appears
    const updatedBubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Message after edit" });
    await expect(updatedBubble).toBeVisible();

    // Verify the "(edited)" badge is shown
    await expect(
      updatedBubble.locator(".thread-event__edited-badge"),
    ).toBeVisible();
    await expect(
      updatedBubble.locator(".thread-event__edited-badge"),
    ).toContainText("(edited)");

    // Verify edit mode is dismissed
    await expect(
      page.locator(".thread-event-input__edit-banner"),
    ).not.toBeVisible();
    await expect(
      page
        .locator(".thread-event-input")
        .getByRole("button", { name: "Send" }),
    ).toBeVisible();
  });

  test("should cancel editing with Escape key", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // Send a fresh message to edit then cancel
    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Message to cancel edit");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Message to cancel edit" });
    await expect(bubble).toBeVisible();

    // Hover and click Edit
    await bubble.locator(".thread-event__bubble").hover();
    await bubble.getByRole("button", { name: "Edit" }).click();

    // Verify we are in edit mode
    await expect(page.locator(".thread-event-input__edit-banner")).toBeVisible();

    await expect(imInput).toHaveValue("Message to cancel edit");

    // Modify content but do NOT save
    await imInput.fill("This should not be saved");

    // Press Escape to cancel
    await imInput.press("Escape");

    // Verify edit mode is dismissed
    await expect(
      page.locator(".thread-event-input__edit-banner"),
    ).not.toBeVisible();

    // Verify Send button is back (not Save)
    await expect(
      page
        .locator(".thread-event-input")
        .getByRole("button", { name: "Send" }),
    ).toBeVisible();

    // Verify input is cleared
    await expect(imInput).toHaveValue("");

    // Verify the original message was NOT changed
    await expect(bubble).toBeVisible();
    await expect(
      page
        .locator(".thread-event--im")
        .filter({ hasText: "This should not be saved" }),
    ).not.toBeVisible();
  });

  test("should delete an IM after confirmation", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // Send a message specifically for deletion
    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Message to delete");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Message to delete" });
    await expect(bubble).toBeVisible();

    // Hover and click Delete
    await bubble.locator(".thread-event__bubble").hover();
    await bubble.getByRole("button", { name: "Delete" }).click();

    // Confirmation modal appears
    const confirmModal = page.getByRole("dialog");
    await expect(confirmModal).toBeVisible();
    await expect(confirmModal.getByText("Delete internal comment")).toBeVisible();

    // Confirm deletion
    await confirmModal.getByRole("button", { name: "Delete" }).click();
    await page.waitForLoadState("networkidle");

    // Verify the message is gone
    await expect(bubble).not.toBeVisible();
  });

  test("should render links in IM as clickable anchors", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // Send a message containing a URL
    const imInput = page.getByPlaceholder("Add internal comment...");
    await imInput.fill("Check https://example.com for details");
    await imInput.press("Enter");
    await page.waitForLoadState("networkidle");

    // Locate the bubble
    const bubble = page
      .locator(".thread-event--im")
      .filter({ hasText: "Check" })
      .filter({ hasText: "for details" });
    await expect(bubble).toBeVisible();

    // Verify the URL is rendered as a clickable link
    const link = bubble.locator("a[href='https://example.com']");
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
    await expect(link).toHaveText("https://example.com");
  });

  test("should surface a new IM mention in the mailbox list and thread entry", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // Extract the thread ID from the URL we just landed on so we can POST
    // directly against the thread-events endpoint.
    const threadMatch = page.url().match(/\/thread\/([0-9a-f-]+)/i);
    const threadId = threadMatch?.[1];
    expect(threadId, "thread id should be present in URL").toBeTruthy();

    // Reuse the existing browser session cookies — they carry both the
    // session id and the CSRF token expected by DRF on unsafe verbs.
    const cookies = await page.context().cookies();
    const csrfToken = cookies.find((c) => c.name === "csrftoken")?.value ?? "";

    // Fetch the current user so we can mention ourselves. The UI filters
    // out self-mentions, but the backend allows them — POSTing directly is
    // the simplest way to create an unread mention visible to the test user.
    const meResponse = await page.request.get(`${API_URL}/api/v1.0/users/me/`);
    expect(meResponse.ok()).toBeTruthy();
    const me = (await meResponse.json()) as { id: string; full_name: string };

    // Create an IM mentioning the current user. sync_mention_user_events
    // runs in the post_save signal and materialises the UserEvent MENTION
    // record that feeds the "Mentioned" folder counter and the thread item
    // badge.
    const createResponse = await page.request.post(
      `${API_URL}/api/v1.0/threads/${threadId}/events/`,
      {
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        data: {
          type: "im",
          data: {
            content: `@[${me.full_name}] please take a look`,
            mentions: [{ id: me.id, name: me.full_name }],
          },
        },
      },
    );
    expect(createResponse.ok()).toBeTruthy();
    const createdEvent = (await createResponse.json()) as { id: string };

    // Sanity-check the backend state directly so that a later UI assertion
    // failure unambiguously points at the frontend (stats query cache,
    // wrong mailbox context, etc.) rather than a missing UserEvent.
    const mailboxIdMatch = page.url().match(/\/mailbox\/([0-9a-f-]+)/i);
    const mailboxId = mailboxIdMatch?.[1];
    expect(mailboxId, "mailbox id should be present in URL").toBeTruthy();
    const statsResponse = await page.request.get(
      `${API_URL}/api/v1.0/threads/stats/`,
      {
        params: {
          mailbox_id: mailboxId!,
          stats_fields: "has_unread_mention",
          has_active: "1",
          has_mention: "1",
        },
      },
    );
    expect(statsResponse.ok()).toBeTruthy();
    const stats = (await statsResponse.json()) as { has_unread_mention: number };
    expect(stats.has_unread_mention).toBe(1);

    // Click the "Refresh" button to force `refetchMailboxes()`. The returned
    // mailbox has `count_unread_mentions=1` which trips the effect in
    // MailboxProvider that invalidates both the threads list and the stats
    // queries — the exact chain of cache updates we need for the UI
    // assertions below to pick up the new mention.
    //
    // We deliberately avoid `page.goto`/`page.reload` here: full page loads
    // hit a Next.js static-export hydration race in MailboxProvider that
    // bounces shared-mailbox sessions back to the personal mailbox.
    await page.getByRole("button", { name: "Refresh" }).click();
    await page.waitForLoadState("networkidle");

    // Primary UI assertion: the thread entry in the thread list shows the
    // "Unread mention" badge. The thread list is re-fetched on navigation,
    // which makes it the most reliable indicator that the mention landed.
    const threadLink = page
      .getByRole("link", { name: "Shared inbox thread for IM" })
      .first();
    await expect(
      threadLink.getByLabel("Unread mention").first(),
    ).toBeVisible();

    // Secondary assertion: the "Mentioned" folder counter in the sidebar.
    // Scoped to `nav.mailbox-list` to disambiguate from any thread subject
    // that might contain "Mentioned". Uses toContainText so trailing
    // whitespace in the span does not fail the match.
    const mentionedCounter = page
      .locator("nav.mailbox-list .mailbox__item")
      .filter({ hasText: "Mentioned" })
      .locator(".mailbox__item-counter");
    await expect(mentionedCounter).toContainText("1");

    // Cleanup: delete the event we created so subsequent tests (and re-runs
    // without db:reset) do not see a lingering unread mention on the thread.
    const deleteResponse = await page.request.delete(
      `${API_URL}/api/v1.0/threads/${threadId}/events/${createdEvent.id}/`,
      { headers: { "X-CSRFToken": csrfToken } },
    );
    expect(deleteResponse.ok()).toBeTruthy();
  });

  test("should hide edit and delete actions once the edit delay has elapsed", async ({
    page,
    browserName,
  }) => {
    await navigateToSharedThread(page, browserName);

    // The e2e_demo management command seeds one pre-aged ThreadEvent per
    // browser user with a browser-scoped content string. That event's
    // `created_at` is pushed 2h into the past, so the backend's
    // `is_editable` returns false and the frontend's `canModify` guard
    // hides Edit/Delete. We locate the bubble by its unique content
    // instead of sending a fresh message and ageing it at runtime.
    const agedContent = `[e2e-aged-${browserName}] Message past edit delay`;
    const agedBubble = page
      .locator(".thread-event--im")
      .filter({ hasText: agedContent });
    await expect(agedBubble).toBeVisible();

    // Hover to try and reveal actions — they must remain hidden because
    // `canModify` in the component is false.
    await agedBubble.locator(".thread-event__bubble").hover();
    await expect(
      agedBubble.getByRole("button", { name: "Edit" }),
    ).toHaveCount(0);
    await expect(
      agedBubble.getByRole("button", { name: "Delete" }),
    ).toHaveCount(0);

    // And the server must reject the write even if the button were forced:
    // this guards against the UI hiding actions while the backend still
    // allows edits (or vice versa).
    const eventDomId = await agedBubble.getAttribute("id");
    const eventId = eventDomId?.replace(/^thread-event-/, "");
    expect(eventId, "event id should be present on aged bubble").toBeTruthy();

    const cookies = await page.context().cookies();
    const csrfToken = cookies.find((c) => c.name === "csrftoken")?.value ?? "";
    const threadMatch = page.url().match(/\/thread\/([0-9a-f-]+)/i);
    const threadId = threadMatch?.[1];
    const updateResponse = await page.request.patch(
      `${API_URL}/api/v1.0/threads/${threadId}/events/${eventId}/`,
      {
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrfToken,
        },
        data: { data: { content: "tampered after delay" } },
      },
    );
    expect(updateResponse.status()).toBe(403);
  });
});
