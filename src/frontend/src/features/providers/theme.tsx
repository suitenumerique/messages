import { createContext, PropsWithChildren, useContext, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { CunninghamProvider, ContextMenuProvider, FooterProps } from "@gouvfr-lasuite/ui-kit";
import { THEME_KEY } from "../config/constants";
import { tokens } from '@/styles/cunningham-tokens'

type CunninghamTheme = keyof typeof tokens.themes;
type ColorScheme = "system" | "light" | "dark";
type Theme = "white-label" | "anct" | "dsfr";
type ThemeVariant = "light" | "dark";
type ThemeWithVariant = 'white-label-light' | 'white-label-dark' | 'anct-light' | 'anct-dark' | 'dsfr-light' | 'dsfr-dark';
type ThemeConfigMap = {
    theme: Theme;
    terms_of_service_url?: string;
    footer?: FooterProps;
}
type ThemeConfig = Omit<ThemeConfigMap, "theme">;

const ThemeContext = createContext<undefined | {
    colorScheme: ColorScheme;
    setColorScheme: (colorScheme: ColorScheme) => void;
    theme: Theme;
    variant: ThemeVariant;
    setVariant: (variant: ThemeVariant) => void;
    themeConfig: ThemeConfig;
    cunninghamTheme: CunninghamTheme;
}>(undefined)

const THEME_CONFIG_ENV: ThemeConfigMap = process.env.NEXT_PUBLIC_THEME_CONFIG
    ? JSON.parse(process.env.NEXT_PUBLIC_THEME_CONFIG) as ThemeConfigMap
    : { theme: "white-label" };

const { theme = 'white-label', ...themeConfig } = THEME_CONFIG_ENV;

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
