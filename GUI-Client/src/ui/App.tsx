import { useEffect, useState } from "react";
import "./App.css";
import Sidebar from "./components/sidebar/sidebar";
import ZoneEditor from "./components/zoneEditor/zoneEditor";

function App() {
  const [activeServer, setActiveServer] = useState<string>("Root");
  useEffect(() => {
    window.electron.subscribeEvent("status", (data) => console.log(data));
  }, []);

  return (
    <>
      <div style={{ display: "flex", height: "100vh", width: "100%" }}>
        <Sidebar activeServer={activeServer} onSelectServer={setActiveServer} />
        <main style={{ flex: 1, padding: "24px", overflowY: "auto" }}>
          <ZoneEditor serverName={activeServer} />
        </main>
      </div>
    </>
  );
}

export default App;
