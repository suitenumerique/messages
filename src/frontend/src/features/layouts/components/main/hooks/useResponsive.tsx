import { useEffect, useState } from "react";
import config from "@/../cunningham";

export const useResponsive = () => {
  const [width, setWidth] = useState(window.innerWidth);
  const mobile = parseInt(
    config.themes.default.theme.breakpoints.mobile.replace("px", "")
  );
  const tablet = parseInt(
    config.themes.default.theme.breakpoints.tablet.replace("px", "")
  );

  const handleWindowSizeChange = () => {
    setWidth(window.innerWidth);
  };

  useEffect(() => {
    window.addEventListener("resize", handleWindowSizeChange);

    return () => {
      window.removeEventListener("resize", handleWindowSizeChange);
    };
  }, []);

  const isMobile = width <= mobile;
  const isTablet = width <= tablet;
  const isDesktop = width > tablet;

  return { isMobile, isTablet, isDesktop };
};
