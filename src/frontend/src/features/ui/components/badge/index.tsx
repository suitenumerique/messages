type BadgeProps = {
    children: string;
}

export const Badge = ({ children }: BadgeProps) => {
    return (
        <div className="badge">
            {children}
        </div>
    )
}