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
    <>
      <div className="zone-header">
        <h2>{serverName} Zone Configuration</h2>

        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {isCreating ? (
            <>
              <input
                type="text"
                placeholder="e.g. project.homelab"
                value={newZoneName}
                onChange={(e) => onSetNewZoneName(e.target.value)}
              />
              <button onClick={onCreateZone} className="btn-save">
                Create
              </button>
              <button
                onClick={() => onSetIsCreating(false)}
                className="btn-delete"
              >
                Cancel
              </button>
            </>
          ) : (
            <>
              <span>Select Zone:</span>
              {availableZones.length > 0 && (
                <select
                  value={selectedZone}
                  onChange={(e) => onSelectZone(e.target.value)}
                  className="zone-list"
                >
                  {availableZones.map((zone) => (
                    <option key={zone} value={zone}>
                      {zone}.zone
                    </option>
                  ))}
                </select>
              )}
              <button onClick={() => onSetIsCreating(true)} className="btn-add">
                + New Zone
              </button>

              <button
                onClick={() => onDeleteZone(selectedZone)}
                className="btn-delete"
                style={{ marginLeft: "10px" }}
              >
                Delete Zone
              </button>
            </>
          )}
        </div>
      </div>

      {!isCreating && availableZones.length > 0 && (
        <div className="zone-globals">
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
      )}
    </>
  );
}
