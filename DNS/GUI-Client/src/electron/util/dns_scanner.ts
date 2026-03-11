import net from "net";
import { getInfrastructureConfig } from "./config_loader.js";
import { Resolver } from "dns/promises";

// Helper to check a specific IP and Port combination
function checkServer(host: string, port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(1000); // Give up after 1 second

    socket.on("connect", () => {
      socket.destroy();
      resolve(true); // Server is alive!
    });

    socket.on("timeout", () => {
      socket.destroy();
      resolve(false);
    });

    socket.on("error", () => {
      socket.destroy();
      resolve(false); // Connection refused (Server is off)
    });

    // Connect to the specific Loopback IP and Port
    socket.connect(port, host);
  });
}

async function checkDnsServer(ip: string): Promise<boolean> {
  const resolver = new Resolver();

  // Point the resolver exclusively at your custom loopback IP
  // (Note: Node's dns module inherently assumes port 53)
  resolver.setServers([ip]);

  try {
    // Send a standard A-record query. It doesn't matter what domain we ask.
    await resolver.resolve4("test.homelab");

    // If we get an IP back, the server is obviously up.
    return true;
  } catch (error: unknown) {
    const dnsError = error as NodeJS.ErrnoException;

    if (dnsError.code === "ETIMEOUT" || dnsError.code === "ECONNREFUSED") {
      return false; // Server is completely offline
    }

    return true;
  }
}

export async function scanDnsServers(): Promise<StatusPayload> {
  const config = getInfrastructureConfig();
  const status: StatusPayload = {};

  for (const [serverName, serverConfig] of Object.entries(config)) {
    if (serverName === "API") {
      const isApiUp: boolean = await checkServer(
        serverConfig.bind_ip,
        serverConfig.bind_port,
      );
      status[serverName] = {
        state: isApiUp ? "On" : "Off",
        ip: serverConfig.bind_ip,
      };
      continue;
    }
    if (!serverConfig.bind_ip) continue;

    const isUp = await checkDnsServer(serverConfig.bind_ip);
    // NOW we store both the state AND the IP!
    status[serverName] = {
      state: isUp ? "On" : "Off",
      ip: serverConfig.bind_ip,
    };
  }

  return status;
}

export async function debugWatchdogConfig() {
  console.log("\n==================================================");
  console.log("[DEBUG] INITIATING WATCHDOG & CONFIG TEST");
  console.log("==================================================");

  // 1. Test the YAML Loader
  console.log("\n1. Parsing YAML Configurations...");
  const config = getInfrastructureConfig();
  console.log(JSON.stringify(config, null, 2));

  // 2. Test the TCP Scanner
  console.log("\n2. Pinging Loopback IPs...");
  await scanDnsServers();

  console.log("\n[DEBUG] TEST COMPLETE.");
  console.log("==================================================\n");
}
