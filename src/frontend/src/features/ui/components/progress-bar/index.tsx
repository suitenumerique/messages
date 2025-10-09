type ProgressBarProps = {
    progress: number;
}

const ProgressBar = ({ progress }: ProgressBarProps) => {
    return (
        <div className="progress-bar">
            <div className="progress-bar__progress" style={{ width: `${progress}%` }} />
        </div>
    )
}

export default ProgressBar;
