import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

const [compatPath, providerJson] = process.argv.slice(2);
if (!compatPath || !providerJson) process.exit(2);

try {
  const requestedProviders = JSON.parse(providerJson);
  if (!Array.isArray(requestedProviders)) process.exit(2);
  const compatUrl = pathToFileURL(compatPath);
  const { findEnvKeys } = await import(compatUrl.href);
  const source = await readFile(new URL("./env-api-keys.js", compatUrl), "utf8");
  const names = new Set(
    [...source.matchAll(/["']([A-Z][A-Z0-9_]+)["']/g)].map((match) => match[1]),
  );
  const probeEnvironment = Object.fromEntries(
    [...names].map((name) => [name, `credential-schema-probe-${name}`]),
  );
  const knownProviders = new Set([
    ...[...source.matchAll(/^\s*(?:"([^"]+)"|'([^']+)'|([A-Za-z0-9_-]+))\s*:\s*["'][A-Z][A-Z0-9_]*["']/gm)].map((match) => match[1] || match[2] || match[3]),
    ...[...source.matchAll(/provider\s*===\s*["']([^"']+)["']/g)].map((match) => match[1]),
  ]);
  const providers = requestedProviders.includes("*")
    ? [...knownProviders].sort()
    : requestedProviders;
  const fields = Object.fromEntries(
    providers.map((provider) => [provider, findEnvKeys(provider, probeEnvironment) ?? []]),
  );
  process.stdout.write(JSON.stringify({ providers: [...knownProviders].sort(), fields }));
} catch {
  process.exit(1);
}
