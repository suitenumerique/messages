import { useAuth } from "@/features/auth";
import { useNavigate } from "@tanstack/react-router";
import { useEffect } from "react";

/**
 * Check if a user is authenticated otherwise redirect to the homepage
 */
const AuthenticatedView = ({ children }: { children: React.ReactNode }) => {
    const { user } = useAuth();
    const navigate = useNavigate();

    useEffect(() => {
        if (user === null) {
            const next = window.location.pathname + window.location.search + window.location.hash;
            navigate({ to: "/", search: { next }, replace: true });
        }
    }, [user, navigate]);

    if (!user) return null;

    return children;
};

export default AuthenticatedView;
