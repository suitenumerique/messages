import { useConfig } from "@/features/providers/config";

export enum FEATURE_KEYS {
    DRIVE = 'drive',
    AI_SUMMARY = 'ai_summary',
    AI_AUTOLABELS = 'ai_autolabels',
    AI_MESSAGES_GENERATION = 'ai_messages_generation',
}

/**
 * A hook to check if a feature is enabled.
 *
 * Several features like ai features or interoperability can be
 * enabled/disabled according to the config. This utility hook
 * to know the state of a feature with ease.
 */
export const useFeatureFlag = (featureKey: FEATURE_KEYS) => {
    const config = useConfig();

    switch (featureKey) {
        case FEATURE_KEYS.DRIVE:
            return config.DRIVE !== undefined;
        case FEATURE_KEYS.AI_SUMMARY:
            return config.AI_ENABLED === true && config.AI_FEATURE_SUMMARY_ENABLED === true;
        case FEATURE_KEYS.AI_AUTOLABELS:
            return config.AI_ENABLED === true && config.AI_FEATURE_AUTOLABELS_ENABLED === true;
        case FEATURE_KEYS.AI_MESSAGES_GENERATION:
            return config.AI_ENABLED === true && config.AI_FEATURE_MESSAGES_GENERATION_ENABLED === true;
        default:
            throw new Error(`Unknown feature key: ${featureKey}`);
    }
}
