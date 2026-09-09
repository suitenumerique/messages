import { Spinner } from "@gouvfr-lasuite/ui-kit";

type PullToRefreshIndicatorProps = {
    /** Wired to `usePullToRefresh().indicatorRef` — the gesture drives it. */
    ref: (node: HTMLElement | null) => void;
};

/**
 * Purely static markup: the gesture writes the revealed height, the spinner
 * opacity (through `--pull-progress`) and the state classes onto this node
 * itself, so pulling never re-renders the list behind it.
 *
 * Kept mounted (collapsed to height 0) even at rest so the release settles
 * through the height transition instead of unmounting.
 */
export const PullToRefreshIndicator = ({ ref }: PullToRefreshIndicatorProps) => (
    <div className="pull-to-refresh" ref={ref} aria-hidden="true">
        <div className="pull-to-refresh__spinner">
            <Spinner size="lg" />
        </div>
    </div>
);
