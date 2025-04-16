import { User as UserType } from "@/features/api/gen/models/user";
import { UserAvatar } from "./UserAvatar";

interface UserProps {
  user: UserType;
}

export const UserRow = ({ user }: UserProps) => {
  return (
    <div className="user-row">
      <UserAvatar user={user} />
      <span className="user-row__name">{user.full_name}</span>
    </div>
  );
};
