import { useAuth } from "@/features/auth/Auth";
import { useRouter } from "next/router";
import { useEffect } from "react";

/**
 * Check if a user is authenticated otherwise redirect to the homepage
 */
const AuthenticatedView = ({ children }: { children: React.ReactNode }) => {
    const { user } = useAuth();
    const router = useRouter();

    useEffect(() => {
        if (user === null) {
            router.replace("/");
        }
    }, [user, router]);

    if (!user) return null;

    return children;
};

export default AuthenticatedView;
