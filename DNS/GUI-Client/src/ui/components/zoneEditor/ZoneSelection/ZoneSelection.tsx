import React from "react";

interface ZoneSelectionProps {
  serverName: string;
  availableZones: string[];
  selectedZone: string;
  origin: string;
  defaultTtl: number;
  isCreating: boolean;
  newZoneName: string;
  onSelectZone: (zone: string) => void;
  onChangeOrigin: (origin: string) => void;
  onChangeTtl: (ttl: number) => void;
  onSetIsCreating: (isCreating: boolean) => void;
  onSetNewZoneName: (name: string) => void;
  onCreateZone: () => void;
  onDeleteZone: (zone: string) => void;
}

export default function ZoneSelection({
  serverName,
  availableZones,
  selectedZone,
  origin,
  defaultTtl,
  isCreating,
  newZoneName,
  onSelectZone,
  onChangeOrigin,
  onChangeTtl,
  onSetIsCreating,
  onSetNewZoneName,
  onCreateZone,
  onDeleteZone,
}: ZoneSelectionProps) {
  return (
    <div className="zone-selection-wrapper">
      {/* --- HEADER & GLOBAL ACTIONS --- */}
      <div className="zone-header">
        <h2>{serverName} Zone Configuration</h2>

        <div className="zone-header-actions">
          {!isCreating && (
            <>
              <button
                onClick={() => onSetIsCreating(true)}
                className="btn-save"
                style={{ width: "auto" }}
              >
                + New Zone
              </button>
              <button
                onClick={() => onDeleteZone(selectedZone)}
                className="btn-delete"
                disabled={!selectedZone}
              >
                Delete Zone
              </button>
            </>
          )}
        </div>
      </div>

      {/* --- NEW: DEDICATED CREATION CARD --- */}
      {isCreating && (
        <div className="zone-folder-container" style={{ marginTop: "15px" }}>
          <div
            className="zone-globals connected-card"
            style={{ display: "flex", alignItems: "flex-end", gap: "15px" }}
          >
            <div className="input-group" style={{ flex: 1, marginBottom: 0 }}>
              <label>Zone Name</label>
              <input
                type="text"
                placeholder="e.g. project.homelab"
                value={newZoneName}
                onChange={(e) => onSetNewZoneName(e.target.value)}
                className="config-input"
                autoFocus
              />
            </div>

            <div style={{ display: "flex", gap: "10px" }}>
              <button onClick={onCreateZone} className="btn-save">
                Create Zone
              </button>
              <button
                onClick={() => onSetIsCreating(false)}
                className="btn-delete"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- FOLDER TABS & CONFIG CARD (Hidden while creating) --- */}
      {!isCreating && availableZones.length > 0 && (
        <div className="zone-folder-container">
          {/* 1. The Tab Bar */}
          <div className="zone-tabs">
            {availableZones.map((zone) => (
              <button
                key={zone}
                className={`zone-tab ${selectedZone === zone ? "active" : ""}`}
                onClick={() => onSelectZone(zone)}
              >
                {zone}.zone
              </button>
            ))}
          </div>

          {/* 2. The Connected Card */}
          <div className="zone-globals connected-card">
            <div className="input-group">
              <label>$ORIGIN</label>
              <input
                type="text"
                value={origin}
                onChange={(e) => onChangeOrigin(e.target.value)}
              />
            </div>
            <div className="input-group">
              <label>$TTL</label>
              <input
                type="number"
                value={defaultTtl}
                onChange={(e) => onChangeTtl(parseInt(e.target.value) || 0)}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
