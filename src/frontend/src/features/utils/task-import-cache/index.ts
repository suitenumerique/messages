import { MESSAGE_IMPORT_TASK_KEY } from "../../config/constants";

/** An helper to read and write ongoing task import id from the local storage */
export class TaskImportCacheHelper {
    readonly mailboxId: string | undefined;
    readonly #key: string;
    readonly #staleTime = 2 * 24 * 60 * 60 * 1000; // 2 days in ms


    constructor(mailboxId: string | undefined) {
        this.mailboxId = mailboxId;
        this.#key = `${MESSAGE_IMPORT_TASK_KEY}_${mailboxId}`;
    }

    #serialize(taskId: string) {
        const expiresAt = Date.now() + this.#staleTime;
        return `${taskId}:${expiresAt}`;
    }

    #deserialize(value: string) {
        const [taskId, expiresAt] = value.split(":");
        return { taskId, expiresAt: parseInt(expiresAt) || 0 };
    }

    get() {
        if (typeof window === "undefined" || !this.mailboxId) return null;
        const value = localStorage.getItem(this.#key);
        if (!value) return null

        const { taskId, expiresAt } = this.#deserialize(value);
        if (Date.now() <= expiresAt) return taskId;

        this.remove();
        return null;
    }

    set(taskId: string) {
        if (typeof window === "undefined" || !this.mailboxId) return;
        localStorage.setItem(this.#key, this.#serialize(taskId));
    }

    remove() {
        if (typeof window === "undefined" || !this.mailboxId) return;
        localStorage.removeItem(this.#key);
    }
}
