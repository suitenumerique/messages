type ContractColorOptions = {
    lightColor?: string;
    darkColor?: string;
}

/**
 * Helper class about colors
 */
export class ColorHelper {
    /**
     * Act like the contrast-color css function
     * https://developer.mozilla.org/en-US/docs/Web/CSS/color_value/contrast-color
     * According the luminance of the provided color, it will pick the best color between lightColor and darkColor
     * As options, you can provide the lightColor and darkColor to use (can be any valid css color)
     * 
     * TODO: Use CSS properties for light and dark colors to be support theme switch
     */
    static getContrastColor(hexColor: string, { lightColor = "#FFFFFF", darkColor = "#000000" }: ContractColorOptions = {}) {
        // Remove the # if present
        const hex = hexColor.slice(1);
        
        // Convert to RGB
        const r = parseInt(hex.substring(0, 2), 16);
        const g = parseInt(hex.substring(2, 4), 16);
        const b = parseInt(hex.substring(4, 6), 16);
        
        // Calculate relative luminance
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        
        return luminance > 0.5 ? darkColor : lightColor
    }
}
