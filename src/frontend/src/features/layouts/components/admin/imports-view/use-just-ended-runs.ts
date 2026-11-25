import { useEffect, useRef, useState } from "react";
import { AdminImportRun, ImportRun, MaildomainsImportsListStatusItem, mailboxesImportsRetrieve } from "@/features/api/gen";
import { importListStatusOf, isProgressing, isTerminal, STATUS_CANCELLED } from "@/hooks/import-status";

/**
 * Runs that just left the list because they ended in a status it does not
 * show, with their final state. Without them a run at 99% would vanish
 * between two polls, leaving the admin to guess how it ended. They stay until
 * the admin changes page, filter or search (`scopeKey`), or acts on them
 * (`dismiss`).
 *
 * `rows` is undefined while the list loads or still shows the rows of the
 * previous scope: those must not be compared with the new scope's.
 */
export const useJustEndedRuns = (
    rows: AdminImportRun[] | undefined,
    scopeKey: string,
    shownStatuses: MaildomainsImportsListStatusItem[],
) => {
    const [endedRuns, setEndedRuns] = useState<AdminImportRun[]>([]);
    const previousRowsRef = useRef<AdminImportRun[]>([]);
    const scopeKeyRef = useRef(scopeKey);

    useEffect(() => {
        if (scopeKeyRef.current !== scopeKey) {
            scopeKeyRef.current = scopeKey;
            previousRowsRef.current = rows ?? [];
            setEndedRuns([]);
            return;
        }
        if (!rows) return;
        const previousRows = previousRowsRef.current;
        previousRowsRef.current = rows;

        const currentIds = new Set(rows.map((row) => row.id));
        const vanished = previousRows.filter(
            (row) => !currentIds.has(row.id) && isProgressing(row.status),
        );
        if (vanished.length === 0) return;

        // A vanished run may also have been cancelled, forgotten, or pushed to
        // the next page: only one that ended out of the shown statuses is kept.
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
            const ended = runs.filter(
                (run): run is AdminImportRun =>
                    run !== null &&
                    isTerminal(run.status) &&
                    run.status !== STATUS_CANCELLED &&
                    !shownStatuses.includes(importListStatusOf(run)),
            );
            if (ended.length === 0) return;
            setEndedRuns((previous) => [
                ...ended,
                ...previous.filter((run) => !ended.some(({ id }) => id === run.id)),
            ]);
        });
    }, [rows, scopeKey, shownStatuses]);

    const dismiss = (id: string) =>
        setEndedRuns((previous) => previous.filter((run) => run.id !== id));

    return { endedRuns, dismiss };
};
