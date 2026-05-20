/**
 * esbuild script for the arXiv Translator Chrome extension.
 *
 * Bundles TypeScript source files into the dist/ directory.
 * Copies static assets (manifest, HTML, CSS) without modification.
 *
 * Usage:
 *   node build.js          # one-shot build
 *   node build.js --watch  # watch mode (auto-rebuild on change)
 */

import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, readdirSync, statSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const watchMode = process.argv.includes("--watch");

// ── Static file copy ─────────────────────────────────────────────────────────

function copyStatic() {
  const copies = [
    ["manifest.json", "dist/manifest.json"],
    ["popup/popup.html", "dist/popup/popup.html"],
    ["popup/popup.css", "dist/popup/popup.css"],
  ];

  for (const [src, dest] of copies) {
    mkdirSync(dirname(join(__dirname, dest)), { recursive: true });
    copyFileSync(join(__dirname, src), join(__dirname, dest));
  }

  // Copy icons directory if it exists
  const iconsDir = join(__dirname, "icons");
  try {
    const icons = readdirSync(iconsDir);
    mkdirSync(join(__dirname, "dist/icons"), { recursive: true });
    for (const icon of icons) {
      if (statSync(join(iconsDir, icon)).isFile()) {
        copyFileSync(join(iconsDir, icon), join(__dirname, "dist/icons", icon));
      }
    }
  } catch {
    // icons/ directory doesn't exist yet — skip
  }
}

// ── esbuild config ───────────────────────────────────────────────────────────

const entryPoints = [
  { in: "background/service-worker.ts", out: "background/service-worker" },
  { in: "popup/popup.tsx", out: "popup/popup" },
];

const buildOptions = {
  entryPoints,
  bundle: true,
  outdir: "dist",
  format: "esm",
  target: "chrome120",
  sourcemap: watchMode ? "inline" : false,
  minify: !watchMode,
  logLevel: "info",
};

if (watchMode) {
  const ctx = await esbuild.context(buildOptions);
  copyStatic();
  await ctx.watch();
  console.log("[esbuild] watching for changes...");
} else {
  await esbuild.build(buildOptions);
  copyStatic();
  console.log("[esbuild] build complete → dist/");
}
