import { ColorHelper } from "@/features/utils/color-helper";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Controller, useFormContext } from "react-hook-form";
import { useTranslation } from "react-i18next";

const COLORS_PALETTE = [
    "#E2E2E2",
    "#E2E0FF",
    "#D7E3F1",
    "#CCE8E8",
    "#C6EADB",
    "#ECDFEC",
    "#F5DDD6",
    "#FADBDD",
    "#F9DDD1",
    "#EEE1C2",
    "#5E5E5E",
    "#3630DF",
    "#326097",
    "#01696F",
    "#016D3D",
    "#864783",
    "#845044",
    "#C0000C",
    "#904B2E",
    "#664F14",
]

export const RhfColorPaletteField = (props: { name: string }) => {
    const { control, setValue, watch } = useFormContext();
    const { t } = useTranslation();
    const [showInputOfHell, setShowInputOfHell] = useState(false);
    const charColor = useMemo(
        () => ColorHelper.getContrastColor(watch('color')),
        [watch('color')]
    );

    return (
        <Controller
            control={control}
            name={props.name}
            render={({ field }) => (
                <div className="color-palette-field">
                    <div>
                        <label
                            className="color-palette-field__label"
                            onClick={(e) => { if (e.altKey) setShowInputOfHell(!showInputOfHell); }}
                        >
                            {t('labels.form.fields.color')}
                        </label>
                        {
                            showInputOfHell && (
                                <label
                                    className="color-palette-field__input-of-hell"
                                    htmlFor="color"
                                    style={{ '--char-color': charColor } as React.CSSProperties}
                                >
                                    <span className="color-palette-field__input-of-hell__icon">a</span>
                                    <input
                                    type="color"
                                    onChange={(e) => setValue(field.name, e.target.value, { shouldDirty: true })}
                                    value={field.value}
                                    />
                                </label>
                            )
                        }
                    </div>
                    <div className="color-palette-field__colors">
                        {COLORS_PALETTE.map((color) => (
                            <button
                                type="button"
                                key={color}
                                className={clsx("color-palette-field__color", field.value === color && "color-palette-field__color--selected")}
                                style={{ '--background-color': color, '--border-color': `${color}AF` } as React.CSSProperties}
                                onClick={() => setValue(field.name, color, { shouldDirty: true })}
                            >
                                <span className="c__offscreen">{color}</span>
                            </button>
                        ))}
                    </div>
                    <input type="hidden" {...field} />
                </div>
            )}
        />
    )
}
