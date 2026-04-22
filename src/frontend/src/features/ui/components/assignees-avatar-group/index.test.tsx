import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { AssigneesAvatarGroup, type AssigneesAvatarGroupUser } from "./index";

vi.mock("@gouvfr-lasuite/ui-kit", () => ({
    UserAvatar: ({ fullName }: { fullName: string }) => (
        <span data-testid="avatar">{fullName}</span>
    ),
}));

const makeUsers = (count: number): AssigneesAvatarGroupUser[] =>
    Array.from({ length: count }, (_, i) => ({
        id: `id-${i}`,
        name: `User ${i}`,
    }));

describe("AssigneesAvatarGroup", () => {
    it("renders nothing when the list is empty", () => {
        const html = renderToStaticMarkup(
            <AssigneesAvatarGroup users={[]} maxAvatars={2} />,
        );
        expect(html).toBe("");
    });

    it("exposes the size via data-size so the overflow circle can mirror avatar dimensions", () => {
        const html = renderToStaticMarkup(
            <AssigneesAvatarGroup
                users={makeUsers(3)}
                maxAvatars={2}
                overflowMode="replace-last"
                size="small"
            />,
        );
        expect(html).toContain('data-size="small"');
    });

    describe("default (extra) overflow mode", () => {
        it("shows all avatars without overflow counter when within the cap", () => {
            const html = renderToStaticMarkup(
                <AssigneesAvatarGroup users={makeUsers(3)} maxAvatars={3} />,
            );
            expect(html.match(/data-testid="avatar"/g)).toHaveLength(3);
            expect(html).not.toContain("assignees-avatar-group__overflow");
        });

        it("caps avatars at maxAvatars and appends the overflow counter", () => {
            const html = renderToStaticMarkup(
                <AssigneesAvatarGroup users={makeUsers(5)} maxAvatars={3} />,
            );
            expect(html.match(/data-testid="avatar"/g)).toHaveLength(3);
            expect(html).toContain(
                '<span class="assignees-avatar-group__overflow" aria-hidden="true">+2</span>',
            );
        });
    });

    describe("replace-last overflow mode", () => {
        it("shows all avatars when within the cap", () => {
            const html = renderToStaticMarkup(
                <AssigneesAvatarGroup
                    users={makeUsers(2)}
                    maxAvatars={2}
                    overflowMode="replace-last"
                />,
            );
            expect(html.match(/data-testid="avatar"/g)).toHaveLength(2);
            expect(html).not.toContain("assignees-avatar-group__overflow");
        });

        it("replaces the last avatar with the overflow counter", () => {
            const html = renderToStaticMarkup(
                <AssigneesAvatarGroup
                    users={makeUsers(5)}
                    maxAvatars={2}
                    overflowMode="replace-last"
                />,
            );
            expect(html.match(/data-testid="avatar"/g)).toHaveLength(1);
            expect(html).toContain(
                '<span class="assignees-avatar-group__overflow" aria-hidden="true">+4</span>',
            );
        });

        it("still shows one avatar + counter when exactly (maxAvatars+1)", () => {
            const html = renderToStaticMarkup(
                <AssigneesAvatarGroup
                    users={makeUsers(3)}
                    maxAvatars={2}
                    overflowMode="replace-last"
                />,
            );
            expect(html.match(/data-testid="avatar"/g)).toHaveLength(1);
            expect(html).toContain("+2");
        });
    });
});
