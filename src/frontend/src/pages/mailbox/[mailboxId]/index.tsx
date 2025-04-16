import { MainLayout } from "@/features/layouts/components/main";
import { ThreadPanel } from "@/features/layouts/components/thread-panel";

const Mailbox = () => {
    return (
        <div className="threads__container">
            <div className="thread-list-panel">
                <ThreadPanel />
            </div>
        </div>
    )
}

Mailbox.getLayout = function getLayout(page: React.ReactElement) {
    return (
        <MainLayout>
            {page}
        </MainLayout>
    )
}

export default Mailbox;