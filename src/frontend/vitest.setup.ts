import { resolveConfig } from "@/features/config/resolve";
import { initI18n } from "@/features/i18n/initI18n";

// i18n used to be initialized as an import side effect; it is now explicitly
// initialized during bootstrap. Tests get the same default-config setup here.
initI18n(resolveConfig());
