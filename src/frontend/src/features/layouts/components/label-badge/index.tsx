import { Badge } from "@/features/ui/components/badge"
import { Label } from "@/features/api/gen/models/label"

type LabelBadgeProps = {
    label: Label
    size?: "small" | "medium"
}

export const LabelBadge = ({ label, size = "medium" }: LabelBadgeProps) => {
    return (
        <Badge>
            <span
                style={{
                    backgroundColor: label.color,
                    color: getContrastColor(label.color),
                    padding: size === "small" ? "0.125rem 0.375rem" : "0.25rem 0.5rem",
                    borderRadius: "4px",
                    fontSize: size === "small" ? "0.75rem" : "0.875rem",
                    fontWeight: 600,
                    display: "inline-block",
                }}
            >
                {label.name}
            </span>
        </Badge>
    )
}

// Helper function to determine if text should be black or white based on background color
function getContrastColor(hexColor: string): string {
    // Remove the # if present
    const hex = hexColor.replace("#", "")
    
    // Convert to RGB
    const r = parseInt(hex.substring(0, 2), 16)
    const g = parseInt(hex.substring(2, 4), 16)
    const b = parseInt(hex.substring(4, 6), 16)
    
    // Calculate relative luminance
    const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    
    // Return black for light colors, white for dark colors
    return luminance > 0.5 ? "#000000" : "#FFFFFF"
} 