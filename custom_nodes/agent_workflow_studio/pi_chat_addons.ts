/** Compose the user's installed packages through Pi's public APIs. No vendoring. */
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { CONFIG_DIR_NAME, DefaultPackageManager, getAgentDir, SettingsManager, type ExtensionAPI, type ExtensionContext } from "@earendil-works/pi-coding-agent";

type Manifest = {name:string;version:string;pi:{extensions:string[];skills?:string[]}};
const names = ["sol-pi", "@dietrichgebert/ponytail"];

export async function loadChatAddons(pi: ExtensionAPI) {
  // Preserve the operator's package/config choices even when provider auth uses
  // a temporary Keychain profile. Never copy their credentials or change config.
  const parent = process.env.COMFY_PI_PARENT_AGENT_DIR;
  const agentDir = parent === undefined ? getAgentDir() : parent ? resolve(parent.replace(/^~(?=\/|$)/,homedir())) : join(homedir(),CONFIG_DIR_NAME,"agent");
  const settingsManager = SettingsManager.create(homedir(),agentDir);
  const manager = new DefaultPackageManager({cwd:homedir(),agentDir,settingsManager});
  const packages = new Map<string,{root:string;manifest:Manifest}>();
  for (const pkg of manager.listConfiguredPackages()) {
    if (pkg.scope !== "user" || !pkg.installedPath) continue;
    let manifest:Manifest;
    try {manifest=JSON.parse(readFileSync(join(pkg.installedPath,"package.json"),"utf8"));} catch {continue;}
    if (!names.includes(manifest.name)) continue;
    if (packages.has(manifest.name)) throw new Error(`Multiple installed ${manifest.name} packages; choose one in Pi settings.`);
    if (manifest.pi?.extensions?.length !== 1) throw new Error(`Expected the installed ${manifest.name} package's single Pi entrypoint.`);
    packages.set(manifest.name,{root:pkg.installedPath,manifest});
  }
  for (const name of names) if (!packages.has(name)) throw new Error(`Install/configure ${name} in your Pi profile first. Chat never downloads packages.`);
  const sol=packages.get(names[0]!)!,pony=packages.get(names[1]!)!;
  const solURL=pathToFileURL(resolve(sol.root,sol.manifest.pi.extensions[0]!));
  const solModule=await import(solURL.href);
  const configModule=await import(new URL("./config.ts",solURL).href);
  let config:Record<string,unknown>|undefined;
  // Use SoL-Pi's own loader and factory, including project trust/strict validation.
  solModule.createSolPiExtension((ctx:ExtensionContext)=>{
    config=configModule.loadSolPiConfig(ctx.cwd,agentDir,ctx.isProjectTrusted());
    return config;
  })(pi);
  const ponyModule=await import(pathToFileURL(resolve(pony.root,pony.manifest.pi.extensions[0]!)).href);
  ponyModule.default(pi);
  pi.on("resources_discover",()=>({skillPaths:(pony.manifest.pi.skills??[]).map(path=>resolve(pony.root,path))}));
  return (ctx:ExtensionContext)=>{
    if (!config) throw new Error("SoL-Pi did not initialize in this session.");
    if (config.observationPack && !pi.getActiveTools().includes("obs_recall")) throw new Error("SoL-Pi ObservationPack is configured but its recall tool is inactive.");
    return {
      sol_pi:{version:sol.manifest.version,actionFusion:config.actionFusion,observationPack:config.observationPack,evidencePreservingReducer:config.evidencePreservingReducer,onlineContextCompact:config.onlineContextCompact},
      ponytail:{version:pony.manifest.version,mode:ponyModule.resolveSessionMode(ctx.sessionManager.getBranch(),ponyModule.readDefaultMode())},
    };
  };
}
