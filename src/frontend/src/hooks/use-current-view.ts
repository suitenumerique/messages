import ViewHelper, { CurrentView } from "@/features/utils/view-helper";
import { useUrlSearchParams } from "@/hooks/use-url-search-params";

/**
 * Which folder the url being rendered shows. Unlike the `ViewHelper.is*View`
 * checks, which read `window.location`, it is already up to date in the
 * render of a folder switch, before the router pushes the new url.
 */
export const useCurrentView = (): CurrentView => {
    const searchParams = useUrlSearchParams();
    return ViewHelper.getView(searchParams);
};
