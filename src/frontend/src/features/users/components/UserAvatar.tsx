import { User as UserType } from "../types";

interface UserAvatarProps {
  user: UserType;
}

export const UserAvatar = ({ user }: UserAvatarProps) => {
  const initials = user.name
    .split(" ")
    .map((name) => name[0])
    .join("")
    .toUpperCase();
  return <div className="user-avatar">{initials}</div>;
};
