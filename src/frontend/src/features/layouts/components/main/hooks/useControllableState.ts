import React, { useEffect } from "react";

// TODO: This hook is from Cunningham, it should be exporter from it.

/**
 * This hook is used to create a state that can be controlled by the parent. If not the state is handled internally.
 *
 * @param defaultValue if not controlled by the parent, this is the default value
 * @param propsValue if controlled by the parent, this is the value
 * @param propsCallback if controlled by the parent, this is the callback to call when the value changes
 */
export const useControllableState = <T>(
  defaultValue: T,
  propsValue?: T,
  propsCallback?: (value: T) => void
): [T, (value: T) => void] => {
  const [state, setState] = React.useState(
    typeof propsValue === "undefined" ? defaultValue : propsValue
  );

  // Bottom-Up.
  const onChange = (value: T) => {
    if (propsCallback) {
      propsCallback(value);
    } else {
      setState(value);
    }
  };

  // Top-Down.
  useEffect(() => {
    if (!propsCallback) {
      return;
    }
    if (typeof propsValue !== "undefined" && propsValue !== state) {
      setState(propsValue);
    }
     
  }, [propsValue]);

  return [state, onChange];
};
