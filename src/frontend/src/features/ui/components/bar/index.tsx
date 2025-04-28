import clsx from "clsx";
import { HTMLAttributes, PropsWithChildren } from "react"

type BarProps = PropsWithChildren<HTMLAttributes<HTMLDivElement>>

/**
 * A simple styled container to put items in a row.
 */
const Bar = ({ children, className,...props }: BarProps) => {
    return (
        <div className={clsx("bar", className)} {...props}>
            {children}
        </div>
    )
}

export default Bar;