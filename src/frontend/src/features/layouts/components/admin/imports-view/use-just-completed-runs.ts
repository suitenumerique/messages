import { useEffect, useRef, useState } from "react";
import { AdminImportRun, ImportRun, mailboxesImportsRetrieve } from "@/features/api/gen";
import { isProgressing, STATUS_COMPLETED } from "@/hooks/import-status";

/**
 * Runs that just left the default view because they completed, with their
 * final state. Without them a run at 99% would vanish between two polls,
 * leaving the admin to guess how it ended. They stay until the admin changes
 * page, filter or search (`scopeKey`), or acts on them (`dismiss`).
 *
 * `rows` is undefined while the list loads or still shows the rows of the
 * previous scope: those must not be compared with the new scope's.
 */
export const useJustCompletedRuns = (
    rows: AdminImportRun[] | undefined,
    scopeKey: string,
    enabled: boolean,
) => {
    const [completedRuns, setCompletedRuns] = useState<AdminImportRun[]>([]);
    const previousRowsRef = useRef<AdminImportRun[]>([]);
    const scopeKeyRef = useRef(scopeKey);

    useEffect(() => {
        if (scopeKeyRef.current !== scopeKey) {
            scopeKeyRef.current = scopeKey;
            previousRowsRef.current = rows ?? [];
            setCompletedRuns([]);
            return;
        }
        if (!rows) return;
        const previousRows = previousRowsRef.current;
        previousRowsRef.current = rows;
        if (!enabled) return;

        const currentIds = new Set(rows.map((row) => row.id));
        const vanished = previousRows.filter(
            (row) => !currentIds.has(row.id) && isProgressing(row.status),
        );
        if (vanished.length === 0) return;

        // A vanished run may also have been cancelled, forgotten, or pushed to
        // the next page: only a completed one is kept.
        Promise.all(
            vanished.map(async (row) => {
                if (!row.mailbox) return null;
                try {
                    const response = await mailboxesImportsRetrieve(row.mailbox, row.id);
                    return { ...row, ...(response.data as ImportRun) };
                } catch {
                    return null;
                }
            }),
        ).then((runs) => {
            const completed = runs.filter(
                (run): run is AdminImportRun =>
                    run !== null && run.status === STATUS_COMPLETED && !run.is_active,
            );
            if (completed.length === 0) return;
            setCompletedRuns((previous) => [
                ...completed,
                ...previous.filter((run) => !completed.some(({ id }) => id === run.id)),
            ]);
        });
    }, [rows, scopeKey, enabled]);

    const dismiss = (id: string) =>
        setCompletedRuns((previous) => previous.filter((run) => run.id !== id));

    return { completedRuns, dismiss };
};
