import type { NextConfig } from "next";

import { buildBackendProxyRewrites } from "./src/config/backend-proxy";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return buildBackendProxyRewrites(process.env.BACKEND_API_URL);
  },
};

export default nextConfig;
