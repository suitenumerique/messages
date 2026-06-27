import { Icon, IconSize, IconType, Spinner } from "@gouvfr-lasuite/ui-kit";
import { Trash } from "@gouvfr-lasuite/ui-kit/icons";
import {
    Button,
    Column,
    DataGrid,
    Input,
    Modal,
    ModalSize,
    Tooltip,
    useModals,
} from "@gouvfr-lasuite/cunningham-react";
import { useTranslation } from "react-i18next";
import { ReactNode, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
    Channel,
    useUsersMeChannelsList,
    useUsersMeChannelsDestroy,
    useUsersMeChannelsPartialUpdate,
    getUsersMeChannelsListQueryKey,
} from "@/features/api/gen";
import { useConfig } from "@/features/providers/config";
import { useAuth } from "@/features/auth";
import {
    currentNativeTokenHash,
    enableNativePush,
    unregisterIfCurrentDevice,
} from "@/features/native/push";
import { isNativePlatform } from "@/features/native/platform";
import { Banner } from "@/features/ui/components/banner";
import { addToast, ToasterItem } from "@/features/ui/components/toaster";
import { handle } from "@/features/utils/errors";
import {
    currentWebPushTokenHash,
    enableWebPush,
    isWebPushSupported,
    unsubscribeIfCurrentBrowser,
} from "./web-push";

// Push channels store their transport in ``settings.platform`` (apns/fcm/web).
// The OS-friendly label lives here in the frontend — the backend deliberately
// keys on transport, not OS (see core.enums.PushPlatformChoices).
const getPlatformLabel = (
    platform: string | undefined,
    t: (key: string) => string,
) => {
    switch (platform) {
        case "apns":
            return t("Apple (iPhone / iPad)");
        case "fcm":
            return t("Android");
        case "web":
            return t("Web browser");
        default:
            return t("Device");
    }
};

const getPlatformIcon = (platform: string | undefined) => {
    switch (platform) {
        case "apns":
            return "phone_iphone";
        case "fcm":
            return "phone_android";
        case "web":
            return "public";
        default:
            return "notifications";
    }
};

const getChannelPlatform = (channel: Channel): string | undefined =>
    (channel.settings as { platform?: string } | null | undefined)?.platform;

/**
 * Lists the current user's registered push devices (user-scoped ``push``
 * channels), lets them enable notifications on the current device — the Web
 * Push flow in a browser, the OS plugin flow inside the native shells — and
 * sign a device out.
 */
export const UserDevicesGrid = () => {
    const { t } = useTranslation();
    const config = useConfig();
    const { user } = useAuth();
    const modals = useModals();
    const queryClient = useQueryClient();
    const [isEnabling, setIsEnabling] = useState(false);
    // The row whose sign-out is in flight: the spinner must sit on that row
    // only, while the mutation's pending flag disables every button (one
    // sign-out at a time).
    const [deletingId, setDeletingId] = useState<string | null>(null);

    // The device being renamed, and the name being typed into its modal.
    const [renamingDevice, setRenamingDevice] = useState<Channel | null>(null);
    const [renameValue, setRenameValue] = useState("");
    // This device's `token_hash`, to recognise its own row in the list.
    const [currentDeviceHash, setCurrentDeviceHash] = useState<string | null>(
        null,
    );

    const { data, isLoading, error } = useUsersMeChannelsList();
    const { mutateAsync: deleteDevice, isPending: isDeleting } =
        useUsersMeChannelsDestroy();
    const { mutateAsync: renameDevice, isPending: isRenaming } =
        useUsersMeChannelsPartialUpdate();

    // ``/users/me/channels/`` returns every user-scoped channel; this view only
    // manages push devices.
    const devices = useMemo(
        () => (data?.data ?? []).filter((c) => c.type === "push"),
        [data],
    );

    // Re-keyed on `devices` so enabling / signing out (which invalidate the
    // list) also refresh whether this device is enrolled.
    useEffect(() => {
        let cancelled = false;
        const resolveHash = isNativePlatform()
            ? currentNativeTokenHash
            : currentWebPushTokenHash;
        void resolveHash().then((hash) => {
            if (!cancelled) setCurrentDeviceHash(hash);
        });
        return () => {
            cancelled = true;
        };
    }, [devices]);

    // Already enrolled: the enable button would only re-upsert the same row,
    // so it is hidden until a sign-out makes it meaningful again.
    const isThisDeviceEnrolled =
        currentDeviceHash !== null &&
        devices.some((device) => device.token_hash === currentDeviceHash);
    // Inside a native shell the OS plugin is always available (it carries its
    // own credentials); a browser can be enabled only when Web Push is
    // configured server-side (VAPID public key) and the engine supports it.
    const canEnableThisDevice =
        !isThisDeviceEnrolled &&
        (isNativePlatform() ||
            (isWebPushSupported() && !!config.PUSH_VAPID_PUBLIC_KEY));

    const invalidateDevices = async () => {
        await queryClient.invalidateQueries({
            queryKey: getUsersMeChannelsListQueryKey(),
            exact: false,
        });
    };

    const handleEnable = async () => {
        setIsEnabling(true);
        try {
            const result = isNativePlatform()
                ? await enableNativePush(user?.id)
                : config.PUSH_VAPID_PUBLIC_KEY
                  ? await enableWebPush(config.PUSH_VAPID_PUBLIC_KEY, user?.id)
                  : "unsupported";
            if (result === "subscribed" || result === "registered") {
                await invalidateDevices();
                addToast(
                    <ToasterItem type="info">
                        <span>{t("Notifications enabled on this device.")}</span>
                    </ToasterItem>,
                );
            } else {
                // One accurate message per failure mode. "denied" needs
                // different guidance per runtime: browser site settings vs the
                // OS app settings (iOS only lets the app prompt once).
                const messages: Record<string, string> = isNativePlatform()
                    ? {
                          denied: t(
                              "Notifications are blocked. Allow them for this app in your device settings.",
                          ),
                          unsupported: t(
                              "This device does not support notifications.",
                          ),
                          registration_failed: t(
                              "Couldn't register this device for notifications. Check your connection and try again.",
                          ),
                      }
                    : {
                          denied: t(
                              "Notifications are blocked. Allow them for this site in your browser settings.",
                          ),
                          dismissed: t(
                              "Notification permission was dismissed. Click again to enable.",
                          ),
                          unsupported: t(
                              "This browser does not support notifications.",
                          ),
                          registration_failed: t(
                              "Couldn't start the notification service worker. Reload the page and try again.",
                          ),
                          push_service_error: t(
                              "Couldn't reach the push service. If you use Brave, enable “Use Google services for push messaging” in settings, restart the browser, then try again.",
                          ),
                      };
                addToast(
                    <ToasterItem type="error">
                        <span>{messages[result]}</span>
                    </ToasterItem>,
                );
            }
        } catch (err) {
            handle(err);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to enable notifications.")}</span>
                </ToasterItem>,
            );
        } finally {
            setIsEnabling(false);
        }
    };

    const handleSignOut = async (channel: Channel) => {
        const decision = await modals.deleteConfirmationModal({
            title: (
                <span className="c__modal__text--centered">
                    {t('Sign out "{{name}}"', { name: channel.name })}
                </span>
            ),
            children: t(
                "This device will stop receiving notifications until you enable them again on it.",
            ),
        });
        if (decision !== "delete") {
            return;
        }
        setDeletingId(channel.id);
        try {
            // If the signed-out row is this very device, tear the local
            // registration down first — otherwise the on-load refresh would
            // re-register it on the next app load, silently undoing the
            // sign-out. Each helper no-ops off its runtime and for a remote
            // device. token_hash is the server's sha256 of the token.
            await unsubscribeIfCurrentBrowser(channel.token_hash, user?.id);
            await unregisterIfCurrentDevice(channel.token_hash, user?.id);
            await deleteDevice({ id: channel.id });
            await invalidateDevices();
            addToast(
                <ToasterItem type="info">
                    <span>{t("Device signed out.")}</span>
                </ToasterItem>,
            );
        } catch (err) {
            handle(err);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to sign out device.")}</span>
                </ToasterItem>,
            );
        } finally {
            setDeletingId(null);
        }
    };

    const openRename = (channel: Channel) => {
        setRenameValue(channel.name ?? "");
        setRenamingDevice(channel);
    };

    const handleRename = async () => {
        if (!renamingDevice) return;
        const name = renameValue.trim();
        if (!name || name === renamingDevice.name) {
            setRenamingDevice(null);
            return;
        }
        try {
            await renameDevice({ id: renamingDevice.id, data: { name } });
            await invalidateDevices();
            setRenamingDevice(null);
        } catch (err) {
            handle(err);
            addToast(
                <ToasterItem type="error">
                    <span>{t("Failed to rename device.")}</span>
                </ToasterItem>,
            );
        }
    };

    const columns: Column<Channel>[] = [
        {
            id: "name",
            headerName: t("Name"),
            // The platform (Apple / Android / Web) is conveyed by the leading
            // icon — labelled for hover and screen readers — so it no longer
            // needs its own column, which the narrow modal can't afford.
            renderCell: ({ row }) => {
                const platformLabel = getPlatformLabel(
                    getChannelPlatform(row),
                    t,
                );
                return (
                    <div
                        className="flex-row flex-align-center"
                        style={{ gap: "var(--c--globals--spacings--xs)" }}
                    >
                        <Tooltip content={platformLabel}>
                            <span
                                className="flex-row flex-align-center"
                                role="img"
                                aria-label={platformLabel}
                            >
                                <Icon
                                    name={getPlatformIcon(getChannelPlatform(row))}
                                    type={IconType.OUTLINED}
                                    size={IconSize.SMALL}
                                    aria-hidden
                                />
                            </span>
                        </Tooltip>
                        <span>{row.name}</span>
                    </div>
                );
            },
        },
        {
            id: "last_active",
            headerName: t("Last active"),
            size: 140,
            // last_used_at is stamped on every (re)registration — relaunch /
            // token refresh — so it reflects "last active". Fall back to
            // created_at for any row that has never been stamped.
            renderCell: ({ row }) => {
                const ts = row.last_used_at ?? row.created_at;
                return ts ? new Date(ts).toLocaleDateString() : "";
            },
        },
        {
            // Icon buttons only, just wide enough so the name column gets the
            // reclaimed width. The header still needs a name: an unlabeled
            // column header reads as nothing to screen readers.
            id: "actions",
            headerName: t("Actions"),
            size: 88,
            renderCell: ({ row }) => (
                <div
                    className="flex-row flex-justify-start"
                    style={{ width: "100%", gap: "var(--c--globals--spacings--2xs)" }}
                >
                    <Button
                        variant="tertiary"
                        size="nano"
                        onClick={() => openRename(row)}
                        disabled={isDeleting || isRenaming}
                        icon={
                            <Icon
                                name="edit"
                                type={IconType.OUTLINED}
                                size={IconSize.SMALL}
                                aria-hidden
                            />
                        }
                        aria-label={t("Rename device")}
                    />
                    <Button
                        color="error"
                        variant="tertiary"
                        size="nano"
                        onClick={() => handleSignOut(row)}
                        disabled={isDeleting}
                        icon={
                            deletingId === row.id ? (
                                <Spinner size="sm" />
                            ) : (
                                <Trash size="small" />
                            )
                        }
                        aria-label={t("Sign out")}
                    />
                </div>
            ),
        },
    ];

    const enableToolbar = canEnableThisDevice ? (
        <div
            className="flex-row flex-justify-end"
            style={{ marginBottom: "var(--c--globals--spacings--sm)" }}
        >
            <Button
                variant="secondary"
                size="small"
                onClick={handleEnable}
                disabled={isEnabling}
                icon={isEnabling ? <Spinner size="sm" /> : undefined}
            >
                {t("Enable notifications on this device")}
            </Button>
        </div>
    ) : null;

    let body: ReactNode;
    if (isLoading) {
        body = (
            <Banner type="info" icon={<Spinner />}>
                {t("Loading devices...")}
            </Banner>
        );
    } else if (error) {
        body = <Banner type="error">{t("Error while loading devices")}</Banner>;
    } else {
        body = (
            <DataGrid
                columns={columns}
                rows={devices}
                onSortModelChange={() => undefined}
                enableSorting={false}
                emptyPlaceholderLabel={
                    <span style={{ textAlign: "center" }}>
                        {t("No devices yet. Enable notifications on this device.")}
                    </span> as unknown as string
                }
            />
        );
    }

    return (
        <div className="admin-data-grid">
            {enableToolbar}
            {body}
            {renamingDevice && (
                <Modal
                    isOpen
                    onClose={() => setRenamingDevice(null)}
                    size={ModalSize.SMALL}
                    title={t("Rename device")}
                    rightActions={
                        <>
                            <Button
                                variant="secondary"
                                size="small"
                                onClick={() => setRenamingDevice(null)}
                            >
                                {t("Cancel")}
                            </Button>
                            <Button
                                size="small"
                                onClick={handleRename}
                                disabled={isRenaming || !renameValue.trim()}
                                icon={
                                    isRenaming ? <Spinner size="sm" /> : undefined
                                }
                            >
                                {t("Rename")}
                            </Button>
                        </>
                    }
                >
                    <Input
                        label={t("Name")}
                        value={renameValue}
                        onChange={(event) => setRenameValue(event.target.value)}
                        maxLength={255}
                        autoFocus
                    />
                </Modal>
            )}
        </div>
    );
};
