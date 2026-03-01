import * as fs from "fs";
import * as path from "path";
import * as yaml from "js-yaml";

// --- Internal App Interfaces ---
// The flat shape we want to use inside our Node/React app
export interface ServerConfig {
  bind_ip: string;
  bind_port: number;
}

export interface InfrastructureConfig {
  Root: ServerConfig;
  TLD: ServerConfig;
  Auth: ServerConfig;
  Resolver: ServerConfig;
  API: ServerConfig;
}

// --- YAML File Interfaces ---
export interface YamlFileWrapper {
  server?: {
    bind_ip?: string;
    bind_port?: number;
    buffer_size?: number;
  };
}

// Helper to safely load and parse a YAML file
function loadYamlConfig<T>(filePath: string): T | null {
  try {
    const fileContents = fs.readFileSync(filePath, "utf8");
    return yaml.load(fileContents) as T;
  } catch (e) {
    console.error(`[!] Failed to load config at ${filePath}:`, e);
    return null;
  }
}

export function getInfrastructureConfig(): InfrastructureConfig {
  const projectRoot = path.join(process.cwd(), "..");
  const configDir = path.join(projectRoot, "configs");

  // Default fallback configuration
  const config: InfrastructureConfig = {
    Root: { bind_ip: "127.0.0.3", bind_port: 53 },
    TLD: { bind_ip: "127.0.0.4", bind_port: 53 },
    Auth: { bind_ip: "127.0.0.5", bind_port: 53 },
    Resolver: { bind_ip: "127.0.0.2", bind_port: 53 },
    API: { bind_ip: "127.0.0.1", bind_port: 8000 },
  };

  const rootConfigPath = path.join(configDir, "root_config.yaml");
  const tldConfigPath = path.join(configDir, "tld_config.yaml");
  const authConfigPath = path.join(configDir, "auth_config.yaml");
  const resolverConfigPath = path.join(configDir, "resolver_config.yaml");

  // 3. Override defaults with real data
  // Notice we cast to <YamlFileWrapper> and use optional chaining (?.)
  const parsedRoot = loadYamlConfig<YamlFileWrapper>(rootConfigPath);
  if (parsedRoot?.server?.bind_ip) {
    config.Root.bind_ip = parsedRoot.server.bind_ip;
    config.Root.bind_port = parsedRoot.server.bind_port || 53;
  }

  const parsedTld = loadYamlConfig<YamlFileWrapper>(tldConfigPath);
  if (parsedTld?.server?.bind_ip) {
    config.TLD.bind_ip = parsedTld.server.bind_ip;
    config.TLD.bind_port = parsedTld.server.bind_port || 53;
  }

  const parsedAuth = loadYamlConfig<YamlFileWrapper>(authConfigPath);
  if (parsedAuth?.server?.bind_ip) {
    config.Auth.bind_ip = parsedAuth.server.bind_ip;
    config.Auth.bind_port = parsedAuth.server.bind_port || 53;
  }

  const parsedResolver = loadYamlConfig<YamlFileWrapper>(resolverConfigPath);
  if (parsedResolver?.server?.bind_ip) {
    config.Resolver.bind_ip = parsedResolver.server.bind_ip;
    config.Resolver.bind_port = parsedResolver.server.bind_port || 53;
  }

  return config;
}
