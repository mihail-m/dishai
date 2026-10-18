import { useEffect, useState } from "react";

export type Theme = "light" | "dark";
const KEY = "dishai.theme";
const query = () => window.matchMedia("(prefers-color-scheme: dark)");

/** The saved choice, or the system setting when the user has not chosen yet. */
export function currentTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === "light" || saved === "dark") return saved;
  } catch { /* storage may be blocked: fall back to the system setting */ }
  return query().matches ? "dark" : "light";
}

/** Theme state for the toggle: applies data-theme to the page and remembers an explicit choice. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(currentTheme);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const toggle = () => setTheme((t) => {
    const next = t === "dark" ? "light" : "dark";
    try { localStorage.setItem(KEY, next); } catch { /* the choice still applies for this visit */ }
    return next;
  });
  return [theme, toggle];
}
