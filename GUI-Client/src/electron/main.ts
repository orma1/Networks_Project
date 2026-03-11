import { app, BrowserWindow, ipcMain } from "electron";
import path from "path";
import { isDev } from "./util.js";
import { getPreloadPath } from "./pathResolver.js";
import { debugWatchdogConfig } from "./util/dns_scanner.js";
import {
  debugDnsScanner,
  registerZoneHandlers,
  startWatchdog,
} from "./ApiManager.js";

app.on("ready", () => {
  const mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    show: false,
    webPreferences: {
      preload: getPreloadPath(),
    },
  });
  mainWindow.maximize();
  mainWindow.show();

  if (isDev()) {
    mainWindow.loadURL("http://localhost:5123");
  } else {
    mainWindow.loadFile(path.join(app.getAppPath(), "/dist-react/index.html"));
  }

  mainWindow.webContents.on("did-finish-load", async () => {
    console.log("[Backend] Frontend is ready. Sending data...");

    startWatchdog(mainWindow);
    registerZoneHandlers();
    if (isDev()) {
      await debugWatchdogConfig();
      await debugDnsScanner();
    }
  });

  ipcMain.handle("getMockData", () => {
    return { key1: "data1" };
  });
});
