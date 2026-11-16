import type { NextConfig } from "next";
const desktopExport = process.env.PRESSCHECK_DESKTOP_EXPORT === "1";
const nextConfig: NextConfig = desktopExport ? {
  output: "export",
  trailingSlash: true,
} : {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${process.env.BACKEND_URL || "http://127.0.0.1:8000"}/api/:path*`,
      },
    ];
  },
};
export default nextConfig;
