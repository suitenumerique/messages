import { UserWithoutAbilities as UserType } from "@/features/api/gen/models/user_without_abilities";
import { UserAvatar } from "./user-avatar";

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
