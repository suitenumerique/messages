import { CellContext } from "@tanstack/react-table";
import { Item } from "@/features/drivers/types";
import { Tooltip } from "@openfun/cunningham-react";
import { timeAgo } from "../../utils/utils";

type ExplorerGridUpdatedAtCellProps = CellContext<Item, Date>;

export const ExplorerGridUpdatedAtCell = (
  params: ExplorerGridUpdatedAtCellProps
) => {
  return (
    <div className="explorer__grid__item__last-update">
      <Tooltip content={params.row.original.updated_at.toLocaleString()}>
        <span>{timeAgo(params.row.original.updated_at)}</span>
      </Tooltip>
    </div>
  );
};
