import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: {
    // Static export disables next/image's optimisation; explicit unoptimized
    // means future <Image> usage doesn't fail the build silently.
    unoptimized: true,
  },
};

export default nextConfig;
