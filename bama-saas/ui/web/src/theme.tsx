/**
 * Light / System / Dark.
 *
 * "System" is the default and is a live subscription, not a one-time read: a user
 * whose OS flips to dark at sunset should not have to reload. Only an explicit
 * choice is persisted, so "follow the system" survives as an intent rather than
 * being frozen into whatever the system happened to be on first visit.
 */
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

export type ThemeChoice = "light" | "system" | "dark";
type Resolved = "light" | "dark";

const KEY = "bama.theme";
const ThemeContext = createContext<{
  choice: ThemeChoice;
  resolved: Resolved;
  setChoice: (c: ThemeChoice) => void;
}>({ choice: "system", resolved: "light", setChoice: () => {} });

function systemTheme(): Resolved {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [choice, setChoice] = useState<ThemeChoice>(
    () => (localStorage.getItem(KEY) as ThemeChoice) ?? "system",
  );
  const [system, setSystem] = useState<Resolved>(systemTheme);

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setSystem(media.matches ? "dark" : "light");
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, []);

  const resolved: Resolved = choice === "system" ? system : choice;

  useEffect(() => {
    document.documentElement.dataset.theme = resolved;
    document.documentElement.style.colorScheme = resolved;
  }, [resolved]);

  const value = useMemo(
    () => ({
      choice,
      resolved,
      setChoice: (next: ThemeChoice) => {
        setChoice(next);
        if (next === "system") localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, next);
      },
    }),
    [choice, resolved],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export const useTheme = () => useContext(ThemeContext);
