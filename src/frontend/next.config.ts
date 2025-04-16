import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  debug: process.env.NODE_ENV === "development",
  reactStrictMode: false,
  // FIXME: Ignore build errors until we have all the model types
  typescript: {
    ignoreBuildErrors: true,
  },
};

export default nextConfig;
