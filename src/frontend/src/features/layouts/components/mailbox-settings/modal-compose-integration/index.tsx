import { Modal, ModalSize, Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, IconSize } from "@gouvfr-lasuite/ui-kit";
import { useTranslation } from "react-i18next";
import { useState, useEffect } from "react";
import { Channel } from "@/features/api/gen";
import { WidgetIntegrationForm } from "./widget-integration-form";
import { useConfig } from "@/features/providers/config";

type ModalComposeIntegrationProps = {
    isOpen: boolean;
    onClose: () => void;
    channel?: Channel;
    onSuccess?: () => void;
};

type ChannelType = "widget" | "api_key";
type ViewState = "select_type" | "form";

type ChannelTypeCardProps = {
    title: string;
    description: string;
    icon: string;
    disabled?: boolean;
    onClick: () => void;
};

type ChannelTypeMetadata = {
    type: ChannelType;
    title: string;
    description: string;
    icon: string;
    disabled?: boolean;
};

const CHANNEL_TYPE_METADATA: Record<ChannelType, ChannelTypeMetadata> = {
    widget: {
        type: "widget",
        title: "Website Widget",
        description: "Add a contact form widget to your website to receive messages directly in your mailbox.",
        icon: "widgets",
    },
    api_key: {
        type: "api_key",
        title: "API Key",
        description: "Generate an API key to send messages programmatically from your applications.",
        icon: "key",
        disabled: true
    },
};

const ChannelTypeCard = ({ title, description, icon, disabled, onClick }: ChannelTypeCardProps) => {
    const { t } = useTranslation();

    return (
        <button
            type="button"
            className={`channel-type-card ${disabled ? "channel-type-card--disabled" : ""}`}
            onClick={onClick}
            disabled={disabled}
        >
            {disabled && (
                <span className="channel-type-card__badge">{t("Coming soon")}</span>
            )}
            <div className="channel-type-card__icon">
                <Icon name={icon} type={IconType.OUTLINED} size={IconSize.LARGE} />
            </div>
            <div className="channel-type-card__content">
                <h3 className="channel-type-card__title">{title}</h3>
                <p className="channel-type-card__description">{description}</p>
            </div>
        </button>
    );
};

const BackButton = ({ onClick }: { onClick: () => void }) => {
    const { t } = useTranslation();
    return (
        <Button
            type="button"
            variant="tertiary"
            size="small"
            icon={<Icon name="arrow_back" type={IconType.OUTLINED} />}
            onClick={onClick}
            aria-label={t("Back")}
        />
    );
};

export const ModalComposeIntegration = ({
    isOpen,
    onClose,
    channel: initialChannel,
    onSuccess,
}: ModalComposeIntegrationProps) => {
    const { t } = useTranslation();
    const config = useConfig();
    const [currentChannel, setCurrentChannel] = useState<Channel | undefined>(initialChannel);
    const isEditing = !!currentChannel;
    const [viewState, setViewState] = useState<ViewState>(isEditing ? "form" : "select_type");
    const [selectedType, setSelectedType] = useState<ChannelType | null>(
        currentChannel?.type as ChannelType | null
    );

    const enabledChannelTypes = (config.FEATURE_MAILBOX_ADMIN_CHANNELS || []) as string[];

    // Reset state when modal opens/closes or channel changes
    useEffect(() => {
        if (isOpen) {
            if (initialChannel) {
                setCurrentChannel(initialChannel);
                setViewState("form");
                setSelectedType(initialChannel.type as ChannelType);
            } else {
                setCurrentChannel(undefined);
                setViewState("select_type");
                setSelectedType(null);
            }
        }
    }, [isOpen, initialChannel]);

    const handleSelectType = (type: ChannelType) => {
        setSelectedType(type);
        setViewState("form");
    };

    const handleBack = () => {
        setViewState("select_type");
        setSelectedType(null);
    };

    const handleSuccess = (newChannel: Channel) => {
        // When a new channel is created, switch to edit mode
        setCurrentChannel(newChannel);
        onSuccess?.();
    };

    const getTitle = () => {
        if (viewState === "select_type") {
            return t("Create a new integration");
        }
        if (selectedType === "widget") {
            return isEditing ? t("Edit Widget") : t("Create a Widget");
        }
        return t("Integrations");
    };

    // Show back button only when in form view after selecting a type (not when editing existing)
    const showBackButton = viewState === "form" && !isEditing;

    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={getTitle()}
            size={ModalSize.LARGE}
            leftActions={showBackButton ? <BackButton onClick={handleBack} /> : undefined}
        >
            <div className="modal-compose-integration">
                {viewState === "select_type" && (
                    <div className="channel-type-selector">
                        <p className="channel-type-selector__subtitle">
                            {t("Choose the type of integration you want to create")}
                        </p>
                        <div className="channel-type-selector__cards">
                            {enabledChannelTypes.map((channelType) => {
                                const metadata = CHANNEL_TYPE_METADATA[channelType as ChannelType];
                                if (!metadata) return null;
                                
                                return (
                                    <ChannelTypeCard
                                        key={channelType}
                                        title={t(metadata.title)}
                                        description={t(metadata.description)}
                                        icon={metadata.icon}
                                        onClick={() => handleSelectType(metadata.type)}
                                        disabled={metadata.disabled}
                                    />
                                );
                            })}
                        </div>
                    </div>
                )}
                {viewState === "form" && selectedType === "widget" && (
                    <WidgetIntegrationForm
                        channel={currentChannel}
                        onSuccess={handleSuccess}
                        onClose={onClose}
                    />
                )}
            </div>
        </Modal>
    );
};
