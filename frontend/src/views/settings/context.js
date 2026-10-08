import { createContext, useContext } from "react";

/**
 * Everything the Settings sections read and write, loaded once by the page
 * (useSettingsData) and handed down here rather than prop-drilled through
 * ten files.
 */
export const SettingsContext = createContext(null);

export function useSettings() {
  const value = useContext(SettingsContext);
  if (!value) throw new Error("useSettings() needs a SettingsContext.Provider above it");
  return value;
}
