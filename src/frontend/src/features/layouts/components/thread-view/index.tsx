
import { useParams } from "next/navigation"
import { ActionBar } from "./components/thread-action-bar"
import { ThreadMessage } from "./components/thread-message"

const TMP_MESSAGES = [{
    id: 1,
    sender_name: "John Doe",
    sender_email: "john.doe@example.com",
    recipients: ["john.doe@example.com", "jane.doe@example.com"],
    date: "2025-04-15T13:36:00Z",
    subject: "Hello, world!",
    raw_html_body: "<p>This is a <strong>test</strong> message.</p>",
    raw_text_body: "This is a test message.",
},{
    id: 2,
    sender_name: "Jane Doe",
    sender_email: "jane.doe@example.com",
    recipients: ["john.doe@example.com", "jane.doe@example.com"],
    date: "2025-04-13T08:32:00Z",
    subject: "Hey 👋",
    raw_html_body: `
<p>Lorem ipsum dolor sit amet, consectetur adipiscing elit. In aliquet purus nec turpis vestibulum posuere. Praesent gravida facilisis tortor sed pellentesque. Integer sodales lectus interdum nulla ultrices lacinia. Integer id neque quis tellus tincidunt porta quis nec orci. Donec placerat risus dui, eu bibendum nibh suscipit ac. Etiam convallis in libero non venenatis. In ultrices magna a magna semper, non tincidunt ex tempor. Nunc condimentum leo dolor. <o>Pellentesque</o> mollis sapien sed <b>dolor malesuada</b>, sit amet semper tellus fermentum.</p>
    <p>Pellentesque habitant morbi tristique senectus et netus et malesuada fames ac turpis egestas. Nunc orci ligula, euismod at nunc vitae, ultricies vulputate libero. In id placerat quam, sed elementum mi. Proin molestie nulla in dolor dapibus tristique. Praesent sit amet dui quis tellus porttitor efficitur. Ut suscipit odio vitae quam fermentum suscipit. Mauris finibus viverra ante, non mattis justo sagittis vitae. Vivamus non congue diam, vitae aliquet lacus. Aenean luctus pretium dui, a <i>lacinia</i> neque condimentum quis. Fusce convallis sagittis tortor, sed efficitur nisi malesuada vitae. Orci varius natoque penatibus et magnis dis parturient montes, nascetur ridiculus mus. In hac habitasse platea dictumst.</p>
    <p>Donec pellentesque augue et ligula pellentesque, eget vehicula nunc interdum. Vivamus lectus odio, finibus ac venenatis at, venenatis nec ante. Vivamus ac lectus gravida, tincidunt orci et, aliquet ligula. In hac habitasse platea dictumst. Nulla aliquet finibus elementum. Curabitur dictum nisi ut tortor ullamcorper euismod. Aenean convallis molestie libero blandit commodo. Donec porta condimentum ipsum, et viverra purus pretium ac. Morbi fringilla egestas velit, at aliquam neque gravida in.</p>
    <p>Phasellus ornare neque ac neque euismod ultricies. Mauris in posuere enim, sit amet suscipit turpis. In hac habitasse platea dictumst. Nunc fringilla ante sed nulla lobortis molestie. Etiam finibus nisi at libero dapibus dapibus. Sed sodales, nibh laoreet luctus fringilla, dolor libero fringilla orci, sed cursus tellus metus vel orci. Pellentesque vitae massa iaculis, volutpat lorem at, accumsan lectus. In sit amet nunc vel leo maximus dignissim quis vitae mi. Aliquam erat volutpat.</p>
    <p>Etiam purus odio, pharetra in massa nec, lobortis rutrum odio. Curabitur vitae malesuada enim. Integer porta sem eget pharetra euismod. Etiam a pharetra risus. Donec interdum feugiat congue. Nulla facilisi. In neque purus, malesuada dictum ante id, rhoncus pellentesque justo. Curabitur erat libero, aliquet at arcu sed, euismod condimentum ligula. Ut rhoncus pharetra sem, eu tempor magna volutpat at. Aenean sodales eleifend tincidunt. Cras sit amet velit at tellus placerat condimentum. Donec vel nisl massa. Mauris tempor at quam et mattis.</p>
`,
    raw_text_body: "This is a test message.",
}]

export const ThreadView = () => {
    const params = useParams<{ mailboxId: string, threadId: string }>()

    if (!params.threadId) {
        return <div className="thread-view thread-view--shrinked"></div>
    }

    return (
        <div className="thread-view">
            <ActionBar />
            <div className="thread-view__messages">
                {TMP_MESSAGES.map((message) => (
                    <ThreadMessage key={message.id} message={message} />
                ))}
            </div>
        </div>
    )
}