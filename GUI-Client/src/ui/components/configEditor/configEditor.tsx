import React, { useEffect, useState } from "react";
// import { ConfigFormat, NameServerConfigFormat, ResolverConfigFormat } from "../../types";
import "./ConfigEditor.css"; // Make sure to import your new CSS file!

interface ConfigEditorProps {
  nameServer: string;
}

export default function ConfigEditor({ nameServer }: ConfigEditorProps) {
  const [config, setConfig] = useState<ConfigFormat | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");

  // ===============================
  //    1. FETCHING LOGIC
  // ===============================
  useEffect(() => {
    if (nameServer === "API") return;

    const loadConfig = async () => {
      setIsLoading(true);
      try {
        const configResult = await window.electron.fetchConfig(nameServer);
        if (configResult) {
          setConfig(configResult);
        }
      } catch (error) {
        console.error("Failed to load config:", error);
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, [nameServer]);

  // ===============================
  //    2. STRICT TYPE UPDATERS
  // ===============================
  const updateServer = <K extends keyof ConfigFormat["server"]>(
    key: K,
    value: ConfigFormat["server"][K],
  ) => {
    setConfig((prev) => {
      if (!prev) return null;
      return { ...prev, server: { ...prev.server, [key]: value } };
    });
  };

  const updateData = <K extends keyof NameServerConfigFormat["data"]>(
    key: K,
    value: NameServerConfigFormat["data"][K],
  ) => {
    setConfig((prev) => {
      if (!prev || !("data" in prev)) return prev;
      return { ...prev, data: { ...prev.data, [key]: value } };
    });
  };

  const updateUpstream = <K extends keyof ResolverConfigFormat["upstream"]>(
    key: K,
    value: ResolverConfigFormat["upstream"][K],
  ) => {
    setConfig((prev) => {
      if (!prev || !("upstream" in prev)) return prev;
      return { ...prev, upstream: { ...prev.upstream, [key]: value } };
    });
  };

  const updateBehavior = <K extends keyof ResolverConfigFormat["behavior"]>(
    key: K,
    value: ResolverConfigFormat["behavior"][K],
  ) => {
    setConfig((prev) => {
      if (!prev || !("behavior" in prev)) return prev;
      return { ...prev, behavior: { ...prev.behavior, [key]: value } };
    });
  };

  const updateStorage = <K extends keyof ResolverConfigFormat["storage"]>(
    key: K,
    value: ResolverConfigFormat["storage"][K],
  ) => {
    setConfig((prev) => {
      if (!prev || !("storage" in prev)) return prev;
      return { ...prev, storage: { ...prev.storage, [key]: value } };
    });
  };

  // ===============================
  //    3. SAVE LOGIC
  // ===============================
  const handleSave = async () => {
    if (!config) return;
    setSaveStatus("saving");
    try {
      const result = await window.electron.saveConfig(nameServer, config);
      if (result.success) {
        setSaveStatus("saved");
      } else {
        console.error("Save failed:", result.error);
        setSaveStatus("error");
      }
    } catch (error) {
      console.error("Unexpected error saving config:", error);
      setSaveStatus("error");
    } finally {
      setTimeout(() => setSaveStatus("idle"), 2000);
    }
  };

  // ===============================
  //    4. RENDER UI
  // ===============================
  if (nameServer === "API") {
    return (
      <div className="config-empty-state">
        <h2>{nameServer}</h2>
        <p>The API server configuration is managed separately.</p>
      </div>
    );
  }

  if (isLoading || !config) return <p>Loading {nameServer} config...</p>;

  return (
    <div className="config-editor">
      <h2>{nameServer} Configuration</h2>

      {/* --- SECTION 1: SERVER (Shared by all) --- */}
      <div className="config-card">
        <h3>Server Settings</h3>
        <label className="config-label">
          Bind IP:
          <input
            type="text"
            className="config-input"
            value={config.server.bind_ip || ""}
            onChange={(e) => updateServer("bind_ip", e.target.value)}
          />
        </label>
        <label className="config-label">
          Bind Port:
          <input
            type="number"
            className="config-input"
            value={config.server.bind_port || 53}
            onChange={(e) =>
              updateServer("bind_port", parseInt(e.target.value) || 53)
            }
          />
        </label>
        <label className="config-label">
          Buffer Size:
          <input
            type="number"
            className="config-input"
            value={config.server.buffer_size || 512}
            onChange={(e) =>
              updateServer("buffer_size", parseInt(e.target.value) || 512)
            }
          />
        </label>
      </div>

      {/* --- SECTION 2: DATA (Only for Name Servers) --- */}
      {"data" in config && (
        <div className="config-card">
          <h3>Data Settings</h3>
          <label className="config-label">
            Zone Directory:
            <input
              type="text"
              className="config-input"
              value={
                (config as NameServerConfigFormat).data.zone_directory || ""
              }
              onChange={(e) => updateData("zone_directory", e.target.value)}
            />
          </label>
        </div>
      )}

      {/* --- SECTION 3: UPSTREAM (Resolver Only) --- */}
      {"upstream" in config && (
        <div className="config-card">
          <h3>Upstream Forwarders</h3>
          <label className="config-label">
            Root Server IP:
            <input
              type="text"
              className="config-input"
              value={
                (config as ResolverConfigFormat).upstream.root_server_ip || ""
              }
              onChange={(e) => updateUpstream("root_server_ip", e.target.value)}
            />
          </label>
          <label className="config-label">
            Root Server Port:
            <input
              type="number"
              className="config-input"
              value={
                (config as ResolverConfigFormat).upstream.root_server_port || 53
              }
              onChange={(e) =>
                updateUpstream(
                  "root_server_port",
                  parseInt(e.target.value) || 53,
                )
              }
            />
          </label>
          <label className="config-label">
            Public Forwarder:
            <input
              type="text"
              className="config-input"
              value={
                (config as ResolverConfigFormat).upstream.public_forwarder || ""
              }
              onChange={(e) =>
                updateUpstream("public_forwarder", e.target.value)
              }
            />
          </label>
          <label className="config-label">
            Public Port:
            <input
              type="number"
              className="config-input"
              value={
                (config as ResolverConfigFormat).upstream.public_port || 53
              }
              onChange={(e) =>
                updateUpstream("public_port", parseInt(e.target.value) || 53)
              }
            />
          </label>
        </div>
      )}

      {/* --- SECTION 4: BEHAVIOR (Resolver Only) --- */}
      {"behavior" in config && (
        <div className="config-card">
          <h3>Behavior Settings</h3>
          <label className="config-label">
            Default TTL:
            <input
              type="number"
              className="config-input"
              value={
                (config as ResolverConfigFormat).behavior.default_ttl || 60
              }
              onChange={(e) =>
                updateBehavior("default_ttl", parseInt(e.target.value) || 60)
              }
            />
          </label>
          <label className="config-label">
            Timeout (Seconds):
            <input
              type="number"
              step="0.1"
              className="config-input"
              value={(config as ResolverConfigFormat).behavior.timeout || 2.0}
              onChange={(e) =>
                updateBehavior("timeout", parseFloat(e.target.value) || 2.0)
              }
            />
          </label>
          <label className="config-label">
            Enable Logging:
            <input
              type="checkbox"
              className="config-input"
              checked={
                (config as ResolverConfigFormat).behavior.enable_logging ||
                false
              }
              onChange={(e) =>
                updateBehavior("enable_logging", e.target.checked)
              }
            />
          </label>
        </div>
      )}

      {/* --- SECTION 5: STORAGE (Resolver Only) --- */}
      {"storage" in config && (
        <div className="config-card">
          <h3>Storage Settings</h3>
          <label className="config-label">
            Cache File Path:
            <input
              type="text"
              className="config-input"
              value={(config as ResolverConfigFormat).storage.cache_file || ""}
              onChange={(e) => updateStorage("cache_file", e.target.value)}
            />
          </label>
          <label className="config-label">
            Save Interval (Seconds):
            <input
              type="number"
              className="config-input"
              value={
                (config as ResolverConfigFormat).storage.save_interval || 10
              }
              onChange={(e) =>
                updateStorage("save_interval", parseInt(e.target.value) || 10)
              }
            />
          </label>
          <label className="config-label">
            Cache Capacity:
            <input
              type="number"
              className="config-input"
              value={
                (config as ResolverConfigFormat).storage.cache_capacity || 1000
              }
              onChange={(e) =>
                updateStorage(
                  "cache_capacity",
                  parseInt(e.target.value) || 1000,
                )
              }
            />
          </label>
        </div>
      )}

      {/* --- ACTION BUTTON --- */}
      <button
        className={`save-button ${saveStatus === "saved" ? "saved-success" : ""}`}
        onClick={handleSave}
        disabled={saveStatus === "saving"}
      >
        {saveStatus === "saving"
          ? "Saving..."
          : saveStatus === "saved"
            ? "Saved!"
            : "Save Config"}
      </button>

      {saveStatus === "error" && (
        <p className="error-text">Failed to save. Check terminal logs.</p>
      )}
    </div>
  );
}
