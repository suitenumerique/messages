import "@blocknote/mantine/style.css";
import "./styles/main.scss";

import { bootstrap } from "./bootstrap";
import { installRandomUUIDPolyfill } from "@/features/utils/uuid";

// Must run before anything renders: third-party components (the ui-kit
// FileUploader) call `crypto.randomUUID()` unguarded, and it is undefined
// outside a secure context.
installRandomUUIDPolyfill();

void bootstrap();
