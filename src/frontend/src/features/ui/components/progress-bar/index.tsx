type ProgressBarProps = {
    progress: number | null;
    indeterminate?: boolean;
}

const ProgressBar = ({ progress, indeterminate }: ProgressBarProps) => {
    const isIndeterminate = indeterminate || progress === null;
    return (
        <div className={`progress-bar${isIndeterminate ? ' progress-bar--indeterminate' : ''}`}>
            <div
                className="progress-bar__progress"
                style={{ width: isIndeterminate ? '100%' : `${progress}%` }}
            />
        </div>
    )
}

export default ProgressBar;
