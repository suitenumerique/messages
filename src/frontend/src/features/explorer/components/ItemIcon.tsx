import { Item, ItemType } from "@/features/drivers/types";
import folderIcon from "@/assets/folder/folder.svg";

import { getMimeCategory, ICONS } from "../utils/mimeTypes";

type ItemIconProps = {
  item: Item;
  size?: "small" | "medium" | "large" | "xlarge";
  type?: "mini" | "normal";
};

export const ItemIcon = ({
  item,
  size = "medium",
  type = "normal",
}: ItemIconProps) => {
  const mimeIcon = getItemIcon(item, type);
  // eslint-disable-next-line @next/next/no-img-element
  return <img src={mimeIcon.src} alt="" className={`item-icon ${size}`} />;
};

export const getItemIcon = (item: Item, type: "normal" | "mini") => {
  if (item.type === ItemType.FOLDER) {
    return folderIcon;
  }
  const category = getMimeCategory(item);
  return ICONS[type][category];
};
