import { useEffect, useState } from "react";
import "./App.css";
import Sidebar from "./components/sidebar/sidebar";
import ZoneEditor from "./components/zoneEditor/zoneEditor";

function App() {
  const [selectedNameServer, setSelectedNameServer] = useState<string>("Root");
  useEffect(() => {
    window.electron.subscribeEvent("status", (data) => console.log(data));
  }, []);

  return (
    <>
      <div style={{ display: "flex", height: "100vh", width: "100%" }}>
        <Sidebar
          activeServer={selectedNameServer}
          onSelectServer={setSelectedNameServer}
        />
        <main style={{ flex: 1, padding: "24px", overflowY: "auto" }}>
          <ZoneEditor
            key={selectedNameServer}
            nameServer={selectedNameServer}
          />
        </main>
      </div>
    </>
  );
}

export default App;
