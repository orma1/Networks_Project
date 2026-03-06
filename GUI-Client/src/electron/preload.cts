const electron = require("electron");

electron.contextBridge.exposeInMainWorld("electron", {
  subscribeEvent: (channel: string, callback: (event: any) => void) => {
    electron.ipcRenderer.on(channel, (_: any, data: any) => {
      callback(data);
    });
  },
  getData: () => electron.ipcRenderer.invoke("getMockData"),
  fetchZoneData: (nameServer: string, zoneName: string) =>
    electron.ipcRenderer.invoke("api:fetch-zone", nameServer, zoneName),

  saveZoneData: (nameServer: string, zoneName: string, payload: any) =>
    electron.ipcRenderer.invoke("api:save-zone", nameServer, zoneName, payload),

  fetchZoneList: (tier: string) =>
    electron.ipcRenderer.invoke("api:fetch-zone-list", tier),

  createNewZone: (nameServer: string, zoneName: string) =>
    electron.ipcRenderer.invoke("create-new-zone", nameServer, zoneName),

  deleteZone: (nameServer: string, zoneName: string) =>
    electron.ipcRenderer.invoke("api:delete-zone", nameServer, zoneName),
} satisfies Window["electron"]);
