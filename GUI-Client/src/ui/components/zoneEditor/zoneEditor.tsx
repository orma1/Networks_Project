"use client";

import React, { useState, useEffect } from "react";
import "./ZoneEditor.css";

interface ZoneEditorProps {
  serverName: string;
}

export default function ZoneEditor({ serverName }: ZoneEditorProps) {
  // --- New States for Folder/Multiple Files ---
  const [availableZones, setAvailableZones] = useState<string[]>([]);
  const [selectedZone, setSelectedZone] = useState<string>("");

  const [origin, setOrigin] = useState("");
  const [defaultTtl, setDefaultTtl] = useState(86400);
  const [records, setRecords] = useState<DnsRecord[]>([]);

  const [isLoading, setIsLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");

  // --- 1. Fetch the LIST of zones for the selected tier ---
  useEffect(() => {
    if (serverName === "API" || serverName === "Resolver") {
      return;
    }

    const loadFileList = async () => {
      // Fetch list of files (e.g., ["mywebsite.custom", "test.homelab"])
      const list = await window.electron.fetchZoneList(serverName);
      setAvailableZones(list);

      // Auto-select the first zone in the folder if it exists
      if (list.length > 0) {
        setSelectedZone(list[0]);
      } else {
        setSelectedZone("");
        setRecords([]); // Clear table if folder is empty
      }
    };

    loadFileList();
  }, [serverName]);

  // --- 2. Fetch the actual DATA when a zone is selected ---
  useEffect(() => {
    if (!selectedZone) return;

    const loadData = async () => {
      setIsLoading(true);
      setOrigin("");
      setDefaultTtl(86400);
      setRecords([]);

      // Fetch using the specific filename selected in the dropdown
      const data = await window.electron.fetchZoneData(selectedZone);

      if (data) {
        setOrigin(data.origin);
        setDefaultTtl(data.defaultTtl);
        setRecords(data.records);
      }

      setIsLoading(false);
    };

    loadData();
  }, [selectedZone]);

  const handleAddRecord = () => {
    const newRecord: DnsRecord = {
      id: crypto.randomUUID(),
      name: "",
      class: "IN",
      type: "A",
      ttl: undefined,
      data: "",
    };
    setRecords((prev) => [...prev, newRecord]);
  };

  const handleUpdateRecord = <K extends keyof DnsRecord>(
    id: string,
    field: K,
    value: DnsRecord[K],
  ) => {
    setRecords((prev) =>
      prev.map((record) =>
        record.id === id ? { ...record, [field]: value } : record,
      ),
    );
  };

  const handleDeleteRecord = (id: string) => {
    setRecords((prev) => prev.filter((record) => record.id !== id));
  };

  const handleSave = async () => {
    setSaveStatus("saving");

    try {
      const payload: ZoneData = {
        origin,
        defaultTtl,
        records: records.map((record) => ({ ...record })),
      };

      // Save using the specific filename, NOT the serverName folder
      const result = await window.electron.saveZoneData(selectedZone, payload);

      if (result.success) {
        setSaveStatus("saved");
      } else {
        console.error(`Error saving: ${result.error}`);
        setSaveStatus("error");
      }
    } catch (error) {
      if (error instanceof Error) {
        console.error(`Unexpected Error: ${error.message}`);
      } else {
        console.error("Unexpected Error:", error);
      }

      setSaveStatus("error");
    } finally {
      setTimeout(() => setSaveStatus("idle"), 2500);
    }
  };

  if (serverName === "API" || serverName === "Resolver") {
    return (
      <div className="zone-empty-state">
        <h2>{serverName}</h2>
        <p>This component does not manage a static zone file.</p>
      </div>
    );
  }

  return (
    <div className="zone-editor-container">
      <div className="zone-header">
        <h2>{serverName} Zone Configuration</h2>
        <p>
          Select Zone: &nbsp;
          {availableZones.length > 0 && (
            <select
              value={selectedZone}
              onChange={(e) => setSelectedZone(e.target.value)}
              className="zone-list"
            >
              {availableZones.map((zone) => (
                <option key={zone} value={zone}>
                  {zone}.zone
                </option>
              ))}
            </select>
          )}
        </p>
      </div>

      {isLoading ? (
        <p>Loading zone data from backend...</p>
      ) : availableZones.length === 0 ? (
        <p>No zone files found in this directory.</p>
      ) : (
        <>
          <div className="zone-globals">
            <div className="input-group">
              <label>$ORIGIN</label>
              <input
                type="text"
                value={origin}
                onChange={(e) => setOrigin(e.target.value)}
                placeholder="e.g. mywebsite.custom."
              />
            </div>
            <div className="input-group">
              <label>$TTL</label>
              <input
                type="number"
                value={defaultTtl}
                onChange={(e) => setDefaultTtl(parseInt(e.target.value) || 0)}
              />
            </div>
          </div>

          <div className="table-container">
            <table className="zone-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Class</th>
                  <th>Type</th>
                  <th>TTL (Opt)</th>
                  <th>Target / Data</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {records.map((record) => (
                  <tr key={record.id}>
                    <td>
                      <input
                        type="text"
                        value={record.name}
                        onChange={(e) =>
                          handleUpdateRecord(record.id, "name", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <select
                        value={record.class}
                        onChange={(e) =>
                          handleUpdateRecord(record.id, "class", e.target.value)
                        }
                      >
                        <option value="IN">IN</option>
                        <option value="CH">CH</option>
                        <option value="HS">HS</option>
                      </select>
                    </td>
                    <td>
                      <select
                        value={record.type}
                        onChange={(e) =>
                          handleUpdateRecord(record.id, "type", e.target.value)
                        }
                      >
                        <option value="SOA">SOA</option>
                        <option value="NS">NS</option>
                        <option value="A">A</option>
                        <option value="CNAME">CNAME</option>
                        <option value="TXT">TXT</option>
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        value={record.ttl || ""}
                        onChange={(e) => {
                          const val = e.target.value
                            ? parseInt(e.target.value)
                            : undefined;
                          handleUpdateRecord(record.id, "ttl", val);
                        }}
                        placeholder="Default"
                      />
                    </td>
                    <td>
                      <input
                        type="text"
                        value={record.data}
                        onChange={(e) =>
                          handleUpdateRecord(record.id, "data", e.target.value)
                        }
                      />
                    </td>
                    <td>
                      <button
                        className="btn-delete"
                        onClick={() => handleDeleteRecord(record.id)}
                      >
                        Delete Record
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="zone-func">
            <button className="btn-add" onClick={handleAddRecord}>
              + Add Record
            </button>

            {/* The Smart Save Button */}
            <button
              className="btn-save"
              onClick={handleSave}
              disabled={isLoading || saveStatus !== "idle"}
              style={{
                backgroundColor:
                  saveStatus === "saved"
                    ? "#c7f9cc"
                    : saveStatus === "error"
                      ? "var(--status-off)"
                      : undefined,
                color: saveStatus === "saved" ? "#000" : undefined, // Ensure text is readable on light green
                transition: "background-color 0.3s ease",
              }}
            >
              {saveStatus === "idle" && "Save Changes"}
              {saveStatus === "saving" && "Saving..."}
              {saveStatus === "saved" && "Saved!"}
              {saveStatus === "error" && "Error"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
