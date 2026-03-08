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

    // === Zones Functions ===
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
    // === Configs ===
    fetchConfig: (configName: string) => Promise<ConfigFormat | null>;
    saveConfig: (
      configName: string,
      ConfigData: ConfigFormat,
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

type NameServerConfigFormat = {
  server: {
    bind_ip: string;
    bind_port: number;
    buffer_size: number;
  };
  data: {
    zone_directory: string;
  };
};

type ResolverConfigFormat = {
  server: {
    bind_ip: string;
    bind_port: number;
    buffer_size: number;
  };
  upstream: {
    root_server_ip: string;
    root_server_port: number;
    public_forwarder: string;
    public_port: number;
  };
  behavior: {
    default_ttl: number;
    timeout: number;
    enable_logging: boolean;
  };
  storage: {
    cache_file: string;
    save_interval: number;
    cache_capacity: number;
  };
};

type ConfigFormat = ResolverConfigFormat | NameServerConfigFormat;

//type ConfigData
