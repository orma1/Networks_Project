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

  // --- THEME STATE & LOGIC ---
  // 1. Lazy initialization: React runs this function ONCE before the first render
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const savedTheme = localStorage.getItem("app-theme") as
      | "light"
      | "dark"
      | null;
    if (savedTheme) return savedTheme;

    if (
      window.matchMedia &&
      window.matchMedia("(prefers-color-scheme: dark)").matches
    ) {
      return "dark";
    }
    return "light"; // Default fallback
  });

  // 2. Sync the DOM whenever the theme state changes
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("app-theme", theme);
  }, [theme]);

  // 3. Keep your Electron subscription in its own clean effect
  useEffect(() => {
    if (window.electron) {
      window.electron.subscribeEvent("status", (data: StatusPayload) => {
        setServers(data);
      });
    }
  }, []);

  const toggleTheme = () => {
    setTheme((prevTheme) => (prevTheme === "light" ? "dark" : "light"));
  };
  // ---------------------------

  const dnsServers = ["Root", "TLD", "Auth"];
  const utilServers = ["Resolver", "API"];

  const renderServerGroup = (groupTitle: string, serverKeys: string[]) => (
    <div className="sidebar-group">
      <div className="sidebar-group-title">{groupTitle}</div>
      <div className="tree-line">
        {serverKeys.map((name) => {
          const data = servers[name];
          if (!data) return null;

          const isActive = activeServer === name;
          const displayName = name === "Auth" ? "AUTH" : name;

          return (
            <div
              key={name}
              className={`server-item ${isActive ? "active" : ""}`}
              onClick={() => onSelectServer(name)}
            >
              <div className="server-name">{displayName}</div>
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

  return (
    <div className="sidebar-container">
      {/* Top Section: Server Lists */}
      <div className="sidebar-content">
        {renderServerGroup("DNS servers", dnsServers)}
        {renderServerGroup("Util servers", utilServers)}
      </div>

      {/* Bottom Section: Theme Toggle */}
      <div className="sidebar-footer">
        <button className="theme-toggle-btn" onClick={toggleTheme}>
          {theme === "light" ? "🌙 Dark Mode" : "☀️ Light Mode"}
        </button>
      </div>
    </div>
  );
}
