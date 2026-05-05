// FORK: copied from @gouvfr-lasuite/ui-kit v0.20.0 ShareModal to add a
// `renderAccessFooter` slot rendered below each access row (to display
// per-mailbox assignable users + "Assign" CTA). All other behavior is
// preserved. Target for upstream contribution.
//
// Differences vs upstream:
//   - Imports use public ui-kit exports only (no ":/components/..." aliases)
//   - Added `renderAccessFooter?: (access) => ReactNode` prop
//   - Added SearchUserItem inline (not exported by ui-kit)
//   - Removed ShareLinkSettings (unused by our consumer, keeps fork lean)
import {
    useState,
    useRef,
    useMemo,
    PropsWithChildren,
    ReactNode,
    useCallback,
    useEffect,
} from "react";
import {
    Button,
    Modal,
    ModalSize,
    useCunningham,
} from "@gouvfr-lasuite/cunningham-react";
import {
    type AccessData,
    type InvitationData,
    QuickSearch,
    type QuickSearchData,
    QuickSearchGroup,
    QuickSearchItemTemplate,
    ShareInvitationItem,
    type DropdownMenuOption,
    type UserData,
    UserRow,
    useResponsive,
} from "@gouvfr-lasuite/ui-kit";
import { InvitationUserSelectorList } from "./invitation-user-selector";
import { ShareMemberItem } from "./share-member-item";

enum ViewMode {
    CANNOT_VIEW = "cannot_view",
    SEARCH = "search",
    EMPTY = "empty",
}

type SearchUserItemProps<UserType> = {
    user: UserData<UserType>;
};

// Reproduced locally because ui-kit does not export it.
const SearchUserItem = <UserType,>({ user }: SearchUserItemProps<UserType>) => {
    // Aliased to `tc` so the i18next-cli parser does not extract Cunningham's
    // own translation keys (e.g. `components.share.*`) into our locale files.
    const { t: tc } = useCunningham();
    // When the searchable entity is a Mailbox (the only consumer of this fork
    // today), `is_identity === false` flags shared mailboxes — wrap the row
    // so the SCSS rule that swaps initials for a `group` glyph also fires
    // here. Cast tolerates non-Mailbox UserType silently.
    const isShared = (user as { is_identity?: boolean }).is_identity === false;
    return (
        <QuickSearchItemTemplate
            testId="search-user-item"
            left={
                <div className={isShared ? "share-modal-extensions__shared-avatar" : undefined}>
                    <UserRow fullName={user.full_name} email={user.email} />
                </div>
            }
            alwaysShowRight={false}
            right={
                <div className="c__search-user-item-right">
                    <span>{tc("components.share.item.add")}</span>
                    <span className="material-icons">add</span>
                </div>
            }
        />
    );
};

type ShareModalInvitationProps<UserType, InvitationType> = {
    invitations?: InvitationData<UserType, InvitationType>[];
    onUpdateInvitation?: (
        invitation: InvitationData<UserType, InvitationType>,
        role: string,
    ) => void;
    onDeleteInvitation?: (
        invitation: InvitationData<UserType, InvitationType>,
    ) => void;
    hasNextInvitations?: boolean;
    onLoadNextInvitations?: () => void;
    invitationRoleTopMessage?: (
        invitation: InvitationData<UserType, InvitationType>,
    ) => string;
};

type ShareModalAccessProps<UserType, AccessType> = {
    accesses?: AccessData<UserType, AccessType>[];
    accessRoleKey?: keyof AccessData<UserType, AccessType>;
    hasNextMembers?: boolean;
    onLoadNextMembers?: () => void;
    onDeleteAccess?: (access: AccessData<UserType, AccessType>) => void;
    onUpdateAccess?: (
        access: AccessData<UserType, AccessType>,
        role: string,
    ) => void;
    accessRoleTopMessage?: (
        access: AccessData<UserType, AccessType>,
    ) => string | ReactNode | undefined;
    /**
     * Fork-only extension: rendered directly below each access row inside
     * the members list. Used for the per-mailbox assignable users list.
     */
    renderAccessFooter?: (
        access: AccessData<UserType, AccessType>,
    ) => ReactNode;
    /**
     * Fork-only extension: rendered on the right side of each access row,
     * inline with the role dropdown. Used to surface an "Assign" CTA on
     * mailbox rows with a single user (no sub-list needed).
     */
    renderAccessRightExtras?: (
        access: AccessData<UserType, AccessType>,
    ) => ReactNode;
    /**
     * Fork-only extension: extra class name applied to the access row
     * wrapper. Used to flag row-level state (e.g. assignment) so CSS can
     * decorate the upstream UserRow's avatar.
     */
    getAccessClassName?: (
        access: AccessData<UserType, AccessType>,
    ) => string | undefined;
};

type ShareModalSearchProps<UserType> = {
    searchUsersResult?: UserData<UserType>[];
    onSearchUsers?: (search: string) => void;
    searchPlaceholder?: string;
    /**
     * Fork-only extension: overrides the heading rendered above the search
     * results group (defaults to Cunningham's
     * `components.share.search.group_name`).
     */
    searchGroupName?: string;
    onInviteUser?: (users: UserData<UserType>[], role: string) => void;
    loading?: boolean;
    /**
     * Fork-only extension: when `false`, typing an email that does not
     * match any search result will NOT surface an "invite" action. Only
     * users returned by `onSearchUsers` can be selected. Defaults to `true`.
     */
    allowInvitation?: boolean;
};

export type ShareModalProps<UserType, InvitationType, AccessType> = {
    modalTitle?: string;
    isOpen: boolean;
    canUpdate?: boolean;
    canView?: boolean;
    cannotViewChildren?: ReactNode;
    cannotViewMessage?: string;
    onClose: () => void;
    invitationRoles?: DropdownMenuOption[];
    getAccessRoles?: (
        access: AccessData<UserType, AccessType>,
    ) => DropdownMenuOption[];
    outsideSearchContent?: ReactNode;
    hideInvitations?: boolean;
    hideMembers?: boolean;
    /**
     * Fork-only: override the default "N members" section heading (which
     * otherwise comes from `useCunningham()` translations).
     */
    membersTitle?: (members: AccessData<UserType, AccessType>[]) => ReactNode;
} & ShareModalInvitationProps<UserType, InvitationType>
    & ShareModalAccessProps<UserType, AccessType>
    & ShareModalSearchProps<UserType>;

export const ShareModal = <UserType, InvitationType, AccessType>({
    searchUsersResult,
    children,
    outsideSearchContent,
    accesses: members = [],
    invitations = [],
    hasNextMembers = false,
    canUpdate = true,
    canView = true,
    hasNextInvitations = false,
    hideInvitations = false,
    hideMembers = false,
    cannotViewChildren,
    renderAccessFooter,
    renderAccessRightExtras,
    getAccessClassName,
    membersTitle,
    allowInvitation = true,
    ...props
}: PropsWithChildren<
    ShareModalProps<UserType, InvitationType, AccessType>
>) => {
    if (!(hideInvitations && hideMembers)) {
        if (!props.invitationRoles) {
            throw new Error("invitationRoles is required");
        }
        if (!props.onSearchUsers) {
            throw new Error("onSearchUsers is required");
        }
    }
    if (!hideInvitations && !props.onInviteUser) {
        throw new Error("onInviteUser is required");
    }
    if (canUpdate && !canView) {
        throw new Error("canView cannot be false if canUpdate is true");
    }

    // Aliased to `tc` so the i18next-cli parser does not extract Cunningham's
    // own translation keys (e.g. `components.share.*`) into our locale files.
    const { t: tc } = useCunningham();
    const { isMobile } = useResponsive();
    const searchUserTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    useEffect(() => {
        return () => {
            if (searchUserTimeoutRef.current) {
                clearTimeout(searchUserTimeoutRef.current);
            }
        };
    }, []);

    const [listHeight, setListHeight] = useState<string>("400px");
    const selectedUsersRef = useRef<HTMLDivElement>(null);
    const [inputValue, setInputValue] = useState<string>("");
    const [searchQuery, setSearchQuery] = useState<string>("");
    const [pendingInvitationUsers, setPendingInvitationUsers] = useState<
        UserData<UserType>[]
    >([]);
    const [selectedInvitationRole, setSelectedInvitationRole] = useState<string>(
        props.invitationRoles?.[0]?.value ?? "",
    );

    const modalContentHeight = !isMobile
        ? "min(690px, calc(100dvh - 2em - 12px - 32px))"
        : `calc(100dvh - 32px)`;

    const onSearchUser = (search: string) => {
        if (searchUserTimeoutRef.current) {
            clearTimeout(searchUserTimeoutRef.current);
        }
        if (search === "") {
            setSearchQuery("");
            props.onSearchUsers!("");
            return;
        }
        searchUserTimeoutRef.current = setTimeout(() => {
            props.onSearchUsers!(search);
            setSearchQuery(search);
        }, 300);
    };

    const onInputChange = (str: string) => {
        setInputValue(str);
        onSearchUser(str);
    };

    const showSearchUsers =
        searchQuery !== "" || pendingInvitationUsers.length > 0;

    const onSelect = useCallback(
        (user: UserData<UserType>) => {
            setPendingInvitationUsers((prev) => [...prev, user]);
            setInputValue("");
            setSearchQuery("");
            props.onSearchUsers!("");
        },
        [props],
    );

    const onRemoveUser = (user: UserData<UserType>) => {
        setPendingInvitationUsers((prev) => prev.filter((u) => u.id !== user.id));
    };

    const usersData: QuickSearchData<UserData<UserType>> = useMemo(() => {
        // TODO(upstream): port this id-based filter back to ui-kit's ShareModal.
        // Upstream uses `.includes(user)` which relies on reference equality;
        // after a search refetch the server returns freshly allocated objects,
        // so already-pending users would reappear in the list and could be
        // picked twice.
        const pendingIds = new Set(pendingInvitationUsers.map((u) => u.id));
        const searchMemberResult = searchUsersResult?.filter(
            (user) => !pendingIds.has(user.id),
        );
        let emptyString: string | undefined =
            searchQuery !== ""
                ? tc("components.share.user.no_result")
                : props.searchPlaceholder ?? tc("components.share.user.placeholder");

        const isValidEmail = (email: string) =>
            !!email.match(
                /^(([^<>()[\]\\.,;:\s@"]+(\.[^<>()[\]\\.,;:\s@"]+)*)|(".+"))@((\[[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\])|(([a-zA-Z\-0-9]+\.)+[a-zA-Z\-0-9]{2,}))$/,
            );

        const isInvitationMode =
            allowInvitation &&
            isValidEmail(searchQuery ?? "") &&
            !searchMemberResult?.some((user) => user.email === searchQuery);

        const newUser = {
            id: searchQuery,
            full_name: "",
            email: searchQuery,
        };

        if (isInvitationMode) {
            emptyString = undefined;
        }

        return {
            groupName:
                props.searchGroupName ?? tc("components.share.search.group_name"),
            elements: searchMemberResult ?? [],
            showWhenEmpty: true,
            emptyString,
            endActions: isInvitationMode
                ? [
                      {
                          content: <SearchUserItem user={newUser} />,
                          onSelect: () => void onSelect(newUser as UserData<UserType>),
                      },
                  ]
                : undefined,
        } satisfies QuickSearchData<UserData<UserType>>;
    }, [searchUsersResult, searchQuery, tc, pendingInvitationUsers, onSelect, allowInvitation, props.searchPlaceholder, props.searchGroupName]);

    const handleRef = (node: HTMLDivElement) => {
        const inputHeight = 70;
        const footerHeight = node?.clientHeight ?? 0;
        const selectedUsersHeight = selectedUsersRef.current?.clientHeight ?? 0;
        const height = `calc(${modalContentHeight} - ${footerHeight}px - ${selectedUsersHeight}px - ${inputHeight}px - 10px)`;
        setListHeight(height);
    };

    const showInvitations =
        !hideInvitations &&
        !showSearchUsers &&
        !props.loading &&
        invitations.length > 0;

    const showMembers =
        !hideMembers && !showSearchUsers && !props.loading && members.length > 0;

    const getViewMode = () => {
        if (!canView) return ViewMode.CANNOT_VIEW;
        if (!(hideInvitations && hideMembers)) return ViewMode.SEARCH;
        return ViewMode.EMPTY;
    };

    const viewMode = getViewMode();

    return (
        <Modal
            title={props.modalTitle ?? tc("components.share.modalTitle")}
            isOpen={props.isOpen}
            onClose={props.onClose}
            aria-label={tc("components.share.modalAriaLabel")}
            closeOnClickOutside
            size={isMobile ? ModalSize.FULL : ModalSize.LARGE}
        >
            <div className="c__share-modal no-padding">
                {canUpdate && pendingInvitationUsers.length > 0 && (
                    <div
                        className="c__share-modal__selected-users"
                        ref={selectedUsersRef}
                    >
                        <InvitationUserSelectorList
                            users={pendingInvitationUsers}
                            onRemoveUser={onRemoveUser}
                            roles={props.invitationRoles!}
                            selectedRole={selectedInvitationRole}
                            onSelectRole={setSelectedInvitationRole}
                            onShare={() => {
                                props.onInviteUser!(
                                    pendingInvitationUsers,
                                    selectedInvitationRole,
                                );
                                setPendingInvitationUsers([]);
                            }}
                        />
                    </div>
                )}

                {viewMode === ViewMode.CANNOT_VIEW && (
                    <div
                        className="c__share-modal__cannot-view"
                        style={{ height: listHeight }}
                    >
                        <div className="c__share-modal__cannot-view__content">
                            <p>
                                {props.cannotViewMessage ??
                                    tc("components.share.cannot_view.message")}
                            </p>
                        </div>
                        {cannotViewChildren}
                    </div>
                )}

                {viewMode === ViewMode.SEARCH && (
                    <QuickSearch
                        onFilter={onInputChange}
                        inputValue={inputValue}
                        showInput={canUpdate}
                        loading={props.loading}
                        placeholder={
                            props.searchPlaceholder ??
                            tc("components.share.user.placeholder")
                        }
                    >
                        <div
                            style={{
                                height: listHeight,
                                overflowY: "auto",
                            }}
                        >
                            {showSearchUsers && (
                                <div
                                    className="c__share-modal__search-users"
                                    data-testid="search-users-list"
                                >
                                    <QuickSearchGroup
                                        group={usersData}
                                        onSelect={(user) => {
                                            onSelect(user);
                                        }}
                                        renderElement={(user) => <SearchUserItem user={user} />}
                                    />
                                </div>
                            )}

                            {!showSearchUsers && children}

                            {showInvitations && (
                                <div
                                    className="c__share-modal__invitations"
                                    data-testid="invitations-list"
                                >
                                    <span className="c__share-modal__invitations-title">
                                        {tc("components.share.invitations.title")}
                                    </span>
                                    {invitations.map((invitation) => (
                                        <ShareInvitationItem
                                            key={invitation.id}
                                            invitation={invitation}
                                            roles={props.invitationRoles!}
                                            updateRole={props.onUpdateInvitation}
                                            deleteInvitation={props.onDeleteInvitation}
                                            canUpdate={canUpdate}
                                            roleTopMessage={props.invitationRoleTopMessage?.(
                                                invitation,
                                            )}
                                        />
                                    ))}
                                    <ShowMoreButton
                                        show={hasNextInvitations}
                                        onShowMore={props.onLoadNextInvitations}
                                    />
                                </div>
                            )}

                            {showMembers && (
                                <div
                                    className="c__share-modal__members"
                                    data-testid="members-list"
                                >
                                    <span className="c__share-modal__members-title">
                                        {membersTitle
                                            ? membersTitle(members)
                                            : tc(
                                                members.length > 1
                                                    ? "components.share.members.title_plural"
                                                    : "components.share.members.title_singular",
                                                { count: members.length },
                                            )}
                                    </span>
                                    {members.map((member) => (
                                        <div key={member.id} className="share-modal-extensions__member">
                                            <ShareMemberItem
                                                accessData={member}
                                                accessRoleKey={props.accessRoleKey ?? "role"}
                                                canUpdate={canUpdate}
                                                roleTopMessage={props.accessRoleTopMessage?.(member)}
                                                roles={
                                                    props.getAccessRoles?.(member) ?? props.invitationRoles!
                                                }
                                                updateRole={props.onUpdateAccess}
                                                deleteAccess={props.onDeleteAccess}
                                                rightExtras={renderAccessRightExtras?.(member)}
                                                wrapperClassName={getAccessClassName?.(member)}
                                            />
                                            {renderAccessFooter?.(member)}
                                        </div>
                                    ))}
                                    <ShowMoreButton
                                        show={hasNextMembers}
                                        onShowMore={props.onLoadNextMembers}
                                    />
                                </div>
                            )}
                        </div>
                    </QuickSearch>
                )}

                <div ref={handleRef}>
                    {!showSearchUsers && outsideSearchContent && (
                        <div className="c__share-modal__footer">{outsideSearchContent}</div>
                    )}
                </div>
            </div>
        </Modal>
    );
};

type ShowMoreButtonProps = {
    show: boolean;
    onShowMore?: () => void;
};

const ShowMoreButton = ({ show, onShowMore }: ShowMoreButtonProps) => {
    // Aliased to `tc` so the i18next-cli parser does not extract Cunningham's
    // own translation keys (e.g. `components.share.*`) into our locale files.
    const { t: tc } = useCunningham();
    if (!show) return null;
    return (
        <div className="c__share-modal__show-more-button">
            <Button
                variant="tertiary"
                size="small"
                icon={<span className="material-icons">arrow_downward</span>}
                onClick={onShowMore}
            >
                {tc("components.share.members.load_more")}
            </Button>
        </div>
    );
};
