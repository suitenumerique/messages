import clsx from "clsx"
import { HTMLAttributes, PropsWithChildren } from "react"

type BadgeProps = PropsWithChildren<HTMLAttributes<HTMLDivElement>>


export const Badge = ({ children, className, ...props }: BadgeProps) => {
    return (
        <div className={clsx("badge", className) } {...props}>
            {children}
        </div>
    )
}
