const electron = require("electron");

electron.contextBridge.exposeInMainWorld("electron", {
  subscribeEvent: (channel: string, callback: (event: any) => void) => {
    electron.ipcRenderer.on(channel, (_: any, data: any) => {
      callback(data);
    });
  },
  getData: () => electron.ipcRenderer.invoke("getMockData"),
  fetchZoneData: (serverName: string) =>
    electron.ipcRenderer.invoke("api:fetch-zone", serverName),

  saveZoneData: (serverName: string, records: any) =>
    electron.ipcRenderer.invoke("api:save-zone", serverName, records),

  fetchZoneList: (tier: string) =>
    electron.ipcRenderer.invoke("api:fetch-zone-list", tier),
}); //satisfies Window["electron"]);
