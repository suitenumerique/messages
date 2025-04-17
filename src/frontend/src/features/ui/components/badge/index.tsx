import { PropsWithChildren } from "react"


export const Badge = ({ children }: PropsWithChildren) => {
    return (
        <div className="badge">
            {children}
        </div>
    )
}