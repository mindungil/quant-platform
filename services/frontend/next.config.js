/** @type {import("next").NextConfig} */
const nextConfig = {
  output: "standalone",
  async rewrites() {
    const gatewayUrl = process.env.API_GATEWAY_BASE_URL || "http://backend:8017";
    return [
      {
        source: "/api/gateway/:path*",
        destination: `${gatewayUrl}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
