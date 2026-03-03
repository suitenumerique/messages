import clsx from "clsx"
import { HTMLAttributes, PropsWithChildren } from "react"

type BadgeProps = PropsWithChildren<HTMLAttributes<HTMLDivElement>> & {
    color?: 'brand' | 'neutral' | 'error' | 'warning' | 'success' | 'info' | 'yellow';
    variant?: 'primary' | 'secondary' | 'tertiary';
    compact?: boolean;
}


export const Badge = ({ children, className, color = 'brand', variant = 'primary', compact = false, ...props }: BadgeProps) => {
    return (
        <div className={clsx("badge", `badge--${color}`, `badge--${variant}`, className, { "badge--compact": compact })} {...props}>
            {children}
        </div>
    )
}
