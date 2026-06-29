import { Modal, ModalSize, Button } from "@gouvfr-lasuite/cunningham-react";
import { Icon, IconType, IconSize } from "@gouvfr-lasuite/ui-kit";
import { ArrowLeft } from "@gouvfr-lasuite/ui-kit/icons";
import { useTranslation } from "react-i18next";
import { useState, useEffect } from "react";
import { Channel, Mailbox } from "@/features/api/gen";
import { WidgetIntegrationForm } from "./widget-integration-form";
import { useConfig } from "@/features/providers/config";
import i18n from "@/features/i18n/initI18n";

type ModalComposeIntegrationProps = {
    isOpen: boolean;
    onClose: () => void;
    mailbox: Mailbox;
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
        title: i18n.t("Website Widget"),
        description: i18n.t("Add a contact form widget to your website to receive messages directly in your mailbox."),
        icon: "widgets",
    },
    api_key: {
        type: "api_key",
        title: i18n.t("API Key"),
        description: i18n.t("Generate an API key to send messages programmatically from your applications."),
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
            color="neutral"
            variant="tertiary"
            size="small"
            icon={<ArrowLeft size={IconSize.SMALL} />}
            onClick={onClick}
            aria-label={t("Back")}
        />
    );
};

export const ModalComposeIntegration = ({
    isOpen,
    onClose,
    mailbox,
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

    // Show back button only when in form view after selecting a type (not when editing existing)
  const showBackButton = viewState === "form" && !isEditing;

  const getTitle = () => {
    let label: string;
        if (viewState === "select_type") {
            label = t("Create a new integration");
        }
        else if (selectedType === "widget") {
            label = isEditing ? t("Edit Widget") : t("Create a Widget");
        }
        else {
            label = t("Integrations");
        }

      return <>
        {showBackButton && <BackButton onClick={handleBack} />}
        {label}
      </>;
    };


    return (
        <Modal
            isOpen={isOpen}
            onClose={onClose}
            title={getTitle()}
            size={ModalSize.LARGE}
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
                        mailbox={mailbox}
                        channel={currentChannel}
                        onSuccess={handleSuccess}
                        onClose={onClose}
                    />
                )}
            </div>
        </Modal>
    );
};
