import deepmerge from "deepmerge";
import {
  cunninghamConfig
} from "@gouvfr-lasuite/ui-kit";

const overrides = {
  components: {
    modal: {
      "tab-sidebar-width": "230px",
    },
  },
};

export default deepmerge(cunninghamConfig, {
    themes: Object.keys(cunninghamConfig.themes).reduce((themes, key) => ({
            ...themes,
            [key]: overrides,
        }), {}),
});
