import * as crypto from "crypto";

// --- 1. The Boilerplate Generator ---
// Add ": ZoneData" right after the parameters to strictly type the return!
export function generateNewZonePayload(zoneName: string): ZoneData {
  // Ensure the origin ends with a dot for DNS compliance
  const formattedName = zoneName.endsWith(".") ? zoneName : `${zoneName}.`;

  // Generate the YYYYMMDD01 serial number dynamically
  const today = new Date();
  const yyyy = today.getFullYear();
  const mm = String(today.getMonth() + 1).padStart(2, "0");
  const dd = String(today.getDate()).padStart(2, "0");
  const serial = `${yyyy}${mm}${dd}01`;

  return {
    origin: formattedName,
    defaultTtl: 86400,
    records: [
      {
        id: crypto.randomUUID(),
        name: "@",
        class: "IN",
        type: "SOA",
        ttl: 86400,
        data: `ns1.${formattedName} admin.${formattedName} ${serial} 21600 3600 604800 86400`,
      },
      {
        id: crypto.randomUUID(),
        name: "@",
        class: "IN",
        type: "NS",
        ttl: 86400,
        data: `ns1.${formattedName}`,
      },
    ],
  };
}
