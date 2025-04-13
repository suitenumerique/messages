import { User as UserType } from "../types";
import { UserAvatar } from "./UserAvatar";

interface UserProps {
  user: UserType;
}

export const UserRow = ({ user }: UserProps) => {
  return (
    <div className="user-row">
      <UserAvatar user={user} />
      <span className="user-row__name">{user.name}</span>
    </div>
  );
};
