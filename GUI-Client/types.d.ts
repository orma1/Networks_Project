type Statistics = {
  cpuUsage: number;
  ramUsage: number;
  storageUsage: number;
};

type StaticData = {
  totalStorage: number;
  cpuModel: string;
  totalMemoryGB: number;
};

type View = "CPU" | "RAM" | "STORAGE";

type FrameWindowAction = "CLOSE" | "MAXIMIZE" | "MINIMIZE";

type EventPayloadMapping = {
  statistics: Statistics;
  getStaticData: StaticData;
  changeView: View;
  sendFrameAction: FrameWindowAction;
};

type UnsubscribeFunction = () => void;

// type Data = {
//   ip: number;
//   id: number;
//   message: string;
// };

interface Window {
  electron: {
    subscribeEvent: (channel: string, callback: (data: Data) => void) => void;
    getData: () => Promise<StaticData>;
    // subscribeChangeView: (callback: (view: View) => void) => void;
    // sendFrameAction: (payload: FrameWindowAction) => void;
    fetchZoneData: (
      serverName: string,
      zoneName: string,
    ) => Promise<ZoneData | null>;
    saveZoneData: (
      serverName: string,
      zoneName: string,
      zoneData: ZoneData,
    ) => Promise<{ success: boolean; error?: string }>;
    fetchZoneList: (tier: string) => Promise<string[]>;
    createNewZone: (
      nameServer: string,
      zoneName: string,
    ) => Promise<{ success: boolean; error?: string }>;
    deleteZone: (
      nameServer: string,
      zoneName: string,
    ) => Promise<{ success: boolean; error?: string }>;
  };
}

type StatusPayload = Record<string, { state: "On" | "Off"; ip: string }>;

type LegalPayloads = StatusPayload | DnsRecord | ZoneData | null;

type fetchZoneList = (tier: string) => Promise<string[]>;

type createNewZone = (
  zoneName: string,
) => Promise<{ success: boolean; error?: string }>;

type DnsRecord = {
  id: string; // Unique ID for React rendering (stripped before saving)
  name: string; // e.g., "@", "ns1", "server1", "www"
  class: string; // e.g., "IN", "CH", "HS" (defaults to IN)
  type: string; // e.g., "SOA", "NS", "A", "CNAME"
  ttl?: number; // Optional: If blank, it inherits the global defaultTtl
  data: string; // e.g., "127.0.0.11" or the long SOA string
};

// The complete Zone File payload
type ZoneData = {
  origin: string; // e.g., "mywebsite.custom."
  defaultTtl: number; // e.g., 86400
  records: DnsRecord[];
};

type UpdateRecordFn = <K extends keyof DnsRecord>(
  id: string,
  field: K,
  value: DnsRecord[K],
) => void;
