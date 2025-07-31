import { useAuth } from "@/features/auth";

export enum Abilities {
    CAN_VIEW_DOMAIN_ADMIN = "view_maildomains",
}

const useAbility = (ability: Abilities) => {
    const { user } = useAuth();

    switch (ability) {
        case Abilities.CAN_VIEW_DOMAIN_ADMIN:
            return user?.abilities[ability] === true;
        default:
            throw new Error(`Ability ${ability} does not exist in Abilities enum.`);
    }
};

export default useAbility;
