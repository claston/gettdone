import { cpSync, mkdirSync, readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const require = createRequire(import.meta.url);
const source = path.dirname(require.resolve("pdfjs-dist/package.json"));
const { version } = JSON.parse(
  readFileSync(path.join(source, "package.json"), "utf8"),
);
const target = path.resolve("public", "pdfjs", version);
mkdirSync(target, { recursive: true });
for (const resource of ["cmaps", "standard_fonts"])
  cpSync(path.join(source, resource), path.join(target, resource), {
    recursive: true,
  });
cpSync(
  path.join(source, "legacy", "build", "pdf.worker.min.mjs"),
  path.join(target, "pdf.worker.min.mjs"),
);
