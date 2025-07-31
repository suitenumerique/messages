import { UserWithoutAbilities as UserType } from "@/features/api/gen/models/user_without_abilities";

interface UserAvatarProps {
  user: UserType;
}

export const UserAvatar = ({ user }: UserAvatarProps) => {
  const initials = user.full_name
    ?.split(" ")
    .map((name) => name[0])
    .join("")
    .toUpperCase();
  return <div className="user-avatar">{initials}</div>;
};
