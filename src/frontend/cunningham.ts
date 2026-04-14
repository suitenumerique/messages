import deepmerge from "deepmerge";
import {
  cunninghamConfig
} from "@gouvfr-lasuite/ui-components";

// Mosa brand color: #0443F2
const brandColors = {
  "brand-500": "#0443F2",
  "brand-550": "#0443F2",
  "brand-600": "#033BD9",
  "brand-650": "#0334C0",
  "logo-1": "#0443F2",
};

export default deepmerge(cunninghamConfig, {
    themes: Object.keys(cunninghamConfig.themes).reduce((themes, key) => ({
            ...themes,
            [key]: {
              components: {
                modal: {
                  "tab-sidebar-width": "230px",
                },
              },
              ...(key === "default" ? { globals: { colors: brandColors } } : {}),
            },
        }), {}),
});
