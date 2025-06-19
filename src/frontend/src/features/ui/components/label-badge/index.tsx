import { Badge } from "@/features/ui/components/badge"
import { ColorHelper } from "@/features/utils/color-helper"
import { ThreadLabel } from "@/features/api/gen"

type LabelBadgeProps = {
    label: ThreadLabel
}

export const LabelBadge = ({ label }: LabelBadgeProps) => {
    const badgeColor = ColorHelper.getContrastColor(label.color!);

    return (
        <Badge title={label.name} style={{ backgroundColor: label.color, color: badgeColor}}>
            {label.name}
        </Badge>
    )
}
