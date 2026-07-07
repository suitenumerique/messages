import { createContext, PropsWithChildren, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CunninghamProvider, ContextMenuProvider } from "@gouvfr-lasuite/ui-kit";
import { THEME_KEY } from "../config/constants";
import { tokens } from '@/styles/cunningham-tokens'
import { ThemeConfig as AppThemeConfig } from "@/features/config/resolve";
import { useConfig } from "./config";

type CunninghamTheme = keyof typeof tokens.themes;
type ColorScheme = "system" | "light" | "dark";
type Theme = AppThemeConfig["theme"];
type ThemeVariant = "light" | "dark";
type ThemeWithVariant = 'white-label-light' | 'white-label-dark' | 'anct-light' | 'anct-dark' | 'dsfr-light' | 'dsfr-dark';
type ThemeConfig = Omit<AppThemeConfig, "theme">;

const ThemeContext = createContext<undefined | {
    colorScheme: ColorScheme;
    setColorScheme: (colorScheme: ColorScheme) => void;
    theme: Theme;
    variant: ThemeVariant;
    setVariant: (variant: ThemeVariant) => void;
    themeConfig: ThemeConfig;
    cunninghamTheme: CunninghamTheme;
}>(undefined)

const CUNNINGHAM_THEME_MAP: Record<ThemeWithVariant, CunninghamTheme> = {
    "white-label-light": "default",
    "white-label-dark": "dark",
    "anct-light": "anct-light",
    "anct-dark": "anct-dark",
    "dsfr-light": "dsfr-light",
    "dsfr-dark": "dsfr-dark",
}


const ThemeProvider = ({ children }: PropsWithChildren) => {
    const { i18n } = useTranslation();
    const { THEME_CONFIG } = useConfig();
    const { theme = 'white-label', ...themeConfig } = THEME_CONFIG;
    const defaultScheme = window.matchMedia("(prefers-color-scheme: dark)")
        .matches
        ? 'dark'
        : 'light';
    const [colorScheme, setColorScheme] = useState<ColorScheme>(localStorage.getItem(THEME_KEY) as ColorScheme | null ?? "light");
    const [variant, setVariant] = useState<ThemeVariant>(colorScheme === "system" ? defaultScheme : colorScheme);
    const cunninghamTheme = CUNNINGHAM_THEME_MAP[`${theme}-${variant}` as ThemeWithVariant];


    const handleThemeChange = (event: MediaQueryListEvent) => {
        const nextVariant = event.matches ? 'dark' : 'light';
        setVariant(nextVariant);
    };

    useEffect(() => {
        localStorage.setItem(THEME_KEY, colorScheme);
        if (colorScheme === "system") {
            const query = window.matchMedia("(prefers-color-scheme: dark)");
            setVariant(query.matches ? 'dark' : 'light');
            query.addEventListener("change", handleThemeChange);

            return () => {
                query.removeEventListener("change", handleThemeChange);
            };
        } else {
            setVariant(colorScheme);
        }
    }, [colorScheme]);

    useEffect(() => {
        document.body.setAttribute("data-theme-variant", variant);
    }, [theme, variant]);

    return (
        <ThemeContext.Provider value={{ colorScheme, setColorScheme, theme, variant, setVariant, themeConfig, cunninghamTheme }}>
            <CunninghamProvider currentLocale={i18n.language} theme={cunninghamTheme}>
                <ContextMenuProvider>
                    {children}
                </ContextMenuProvider>
            </CunninghamProvider>
        </ThemeContext.Provider>
    )
}

export const useTheme = () => {
    const context = useContext(ThemeContext);
    if (!context) throw new Error("useTheme must be used within a ThemeContext.Provider");
    return context;
}

export default ThemeProvider;
