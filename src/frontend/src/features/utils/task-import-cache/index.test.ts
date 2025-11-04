import { beforeEach, describe, expect, it, vi } from "vitest";
import { TaskImportCacheHelper } from ".";

describe("TaskImportCacheHelper", () => {
  const mailboxId = "test-mailbox-123";
  const storageKey = `messages_message-import-task_${mailboxId}`;
  const taskId = "test-task-456";

  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
    // Reset Date.now() mock
    vi.restoreAllMocks();
  });

  describe("constructor", () => {
    it("should set mailboxId", () => {
      const helper = new TaskImportCacheHelper(mailboxId);
      expect(helper.mailboxId).toBe(mailboxId);
    });

    it("should handle undefined mailboxId", () => {
      const helper = new TaskImportCacheHelper(undefined);
      expect(helper.mailboxId).toBeUndefined();
    });
  });

  describe("set", () => {
    it("should store taskId in localStorage with expiration", () => {
      const now = 1000000000000;
      vi.spyOn(Date, "now").mockReturnValue(now);

      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);

      const storedValue = localStorage.getItem(storageKey);
      expect(storedValue).not.toBeNull();

      // Should store taskId:expiresAt format
      const [storedTaskId, expiresAt] = storedValue!.split(":");
      expect(storedTaskId).toBe(taskId);
      // Should expire in 2 days (2 * 24 * 60 * 60 * 1000 ms)
      expect(parseInt(expiresAt)).toBe(now + 2 * 24 * 60 * 60 * 1000);
    });

    it("should not store when mailboxId is undefined", () => {
      const helper = new TaskImportCacheHelper(undefined);
      helper.set(taskId);

      expect(localStorage.length).toBe(0);
    });
  });

  describe("get", () => {
    it("should retrieve valid taskId from localStorage", () => {
      const now = 1000000000000;
      vi.spyOn(Date, "now").mockReturnValue(now);

      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);

      const retrievedTaskId = helper.get();
      expect(retrievedTaskId).toBe(taskId);
    });

    it("should return null when no value is stored", () => {
      const helper = new TaskImportCacheHelper(mailboxId);
      const result = helper.get();

      expect(result).toBeNull();
    });

    it("should return null when mailboxId is undefined", () => {
      const helper = new TaskImportCacheHelper(undefined);
      const result = helper.get();

      expect(result).toBeNull();
    });

    it("should return null and remove expired taskId", () => {
      const now = 1000000000000;
      const dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(now);

      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);
      expect(localStorage.getItem(storageKey)).not.toBeNull();

      // Fast forward time by 2 days + 1 ms
      dateNowSpy.mockReturnValue(1 + now + (2 * 24 * 60 * 60 * 1000));
      expect(helper.get()).toBeNull();

      // Should have removed the expired entry
      expect(localStorage.getItem(storageKey)).toBeNull();
    });

    it("should return taskId when not yet expired", () => {
      const now = 1000000000000;
      const dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(now);

      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);

      // Fast forward time by exactly 2 days (still valid, expires after 2 days)
      dateNowSpy.mockReturnValue(now + 1 * 24 * 60 * 60 * 1000);

      const result = helper.get();
      expect(result).toBe(taskId);
    });

    it("should return taskId at exact expiration time", () => {
      const now = 1000000000000;
      const dateNowSpy = vi.spyOn(Date, "now").mockReturnValue(now);

      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);

      // Fast forward to exact expiration time (2 days)
      dateNowSpy.mockReturnValue(now + 2 * 24 * 60 * 60 * 1000);

      const result = helper.get();
      expect(result).toBe(taskId);
    });
  });

  describe("remove", () => {
    it("should remove taskId from localStorage", () => {
      const helper = new TaskImportCacheHelper(mailboxId);
      helper.set(taskId);

      expect(localStorage.getItem(storageKey)).toBeTruthy();

      helper.remove();

      expect(localStorage.getItem(storageKey)).toBeNull();
    });

    it("should not remove when mailboxId is undefined", () => {
      const helper = new TaskImportCacheHelper(undefined);

      // Manually add something to localStorage
      localStorage.setItem("messages_message-import-task_undefined", "test");

      helper.remove();

      // Should not remove (the method returns early)
      expect(
        localStorage.getItem("messages_message-import-task_undefined")
      ).toBe("test");
    });
  });

  describe("multiple instances", () => {
    it("should handle different mailboxIds independently", () => {
      const mailboxId1 = "mailbox-1";
      const mailboxId2 = "mailbox-2";
      const taskId1 = "task-1";
      const taskId2 = "task-2";

      const helper1 = new TaskImportCacheHelper(mailboxId1);
      const helper2 = new TaskImportCacheHelper(mailboxId2);

      helper1.set(taskId1);
      helper2.set(taskId2);

      expect(helper1.get()).toBe(taskId1);
      expect(helper2.get()).toBe(taskId2);

      helper1.remove();

      expect(helper1.get()).toBeNull();
      expect(helper2.get()).toBe(taskId2);
    });
  });
});
