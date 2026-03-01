import { BrowserWindow, ipcMain } from "electron";
import { scanDnsServers } from "./util/dns_scanner.js";
const mock_data = {
  alert: "DNSSEC BOGUS",
  ip: "6.6.6.6",
  id: 0,
};

const apiHost = "127.0.0.1";
const apiPort = "8000";
let count = 0;

export function get_data(mainWindow: BrowserWindow) {
  mainWindow.webContents.send("keyword", mock_data);
}

export function get_data_interval(mainWindow: BrowserWindow) {
  const replica_mock_data = { ...mock_data };
  setInterval(async () => {
    replica_mock_data.id = mock_data.id + count;
    count++;
    mainWindow.webContents.send("keyword_interval", replica_mock_data);
  }, 1000);
}

// ApiManager.ts

// Controller for UI updates
export async function pushDnsStatusToUI(mainWindow: BrowserWindow) {
  const statusFlags = await scanDnsServers();
  mainWindow.webContents.send("dns_status", statusFlags);
}

export async function pushToUI(
  eventName: string,
  payload: LegalPayloads,
  mainWindow: BrowserWindow,
) {
  mainWindow.webContents.send(eventName, payload);
}

// Dedicated DEBUG Controller (Only logs, doesn't touch UI)
export async function debugDnsScanner() {
  const statusFlags = await scanDnsServers();
  console.log("[DEBUG] DNS Server Status Flags:", statusFlags);
}

let previousState = "";

export function startWatchdog(mainWindow: BrowserWindow, intervalMs = 3000) {
  // Poll the Python servers in the background
  setInterval(async () => {
    const currentStatus = await scanDnsServers();
    const currentStateString = JSON.stringify(currentStatus);
    // Only send the event if the health status actually changed
    if (currentStateString !== previousState) {
      // Shouting down the "server-status" channel
      mainWindow.webContents.send("status", currentStatus);
      previousState = currentStateString;
    }
  }, intervalMs);
}
export function registerZoneHandlers() {
  ipcMain.handle("api:fetch-zone-list", async (_, tier: string) => {
    try {
      const apiUrl = `http://${apiHost}:${apiPort}/api/zones/list/${tier}`;
      const response = await fetch(apiUrl);
      if (!response.ok) return [];
      return await response.json();
    } catch (error) {
      if (error instanceof Error) console.log(error);
      return [];
    }
  });

  ipcMain.handle("api:fetch-zone", async (_, zoneFileName: string) => {
    try {
      const apiUrl = `http://${apiHost}:${apiPort}/api/zone/${zoneFileName}`;
      const response = await fetch(apiUrl);
      if (!response.ok) return null;
      return await response.json();
    } catch (error) {
      console.error("[Backend] Network error fetching zone:", error);
      return null;
    }
  });

  ipcMain.handle(
    "api:save-zone",
    async (_, zoneFileName: string, zoneData: any) => {
      try {
        const apiUrl = `http://${apiHost}:${apiPort}/api/zone/${zoneFileName}`;
        const response = await fetch(apiUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(zoneData),
        });

        if (!response.ok) {
          const errorData = await response.json().catch(() => ({}));
          throw new Error(
            errorData.detail || `FastAPI Error: ${response.statusText}`,
          );
        }
        return { success: true, message: (await response.json()).message };
      } catch (error) {
        if (error instanceof Error)
          return { success: false, error: error.message };
      }
    },
  );
}
