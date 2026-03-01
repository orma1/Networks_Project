import { useEffect, useState } from "react";
import "./Sidebar.css";

interface SidebarProps {
  activeServer: string;
  onSelectServer: (serverName: string) => void;
}

export default function Sidebar({
  activeServer,
  onSelectServer,
}: SidebarProps) {
  const [servers, setServers] = useState<StatusPayload>({
    Root: { state: "Off", ip: "..." },
    TLD: { state: "Off", ip: "..." },
    Auth: { state: "Off", ip: "..." },
    Resolver: { state: "Off", ip: "..." },
    API: { state: "Off", ip: "..." },
  });

  useEffect(() => {
    window.electron.subscribeEvent("status", (data: StatusPayload) => {
      setServers(data);
    });
  }, []);

  return (
    <div className="sidebar-container">
      <h3 className="header">DNS Servers:</h3>

      <div className="tree-line">
        {Object.entries(servers).map(([name, data]) => {
          // 2. Check if this specific item is the active one
          const isActive = activeServer === name;

          return (
            <div
              key={name}
              // 3. Add an onClick handler and an active CSS class
              className={`server-item ${isActive ? "active" : ""}`}
              onClick={() => onSelectServer(name)}
            >
              <div className="server-name">{name}</div>

              <div className="subtext-container">
                <span
                  className="dot"
                  style={{
                    backgroundColor:
                      data.state === "On"
                        ? "var(--status-on)"
                        : "var(--status-off)",
                  }}
                />
                <span className="subtext">
                  {data.state === "On" ? `(online: ${data.ip})` : "(offline)"}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
