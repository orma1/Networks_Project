"use client";

import React, { useState, useEffect } from "react";
import ZoneSelection from "./ZoneSelection/ZoneSelection";
import ZoneTable from "./ZoneTable/ZoneTable";
import ZoneFunctions from "./ZoneFunctions/ZoneFunctions";
import ConfirmModal from "./ConfirmModal/ConfirmModal";

import "./ZoneEditor.css";

interface ZoneEditorProps {
  nameServer: string;
}

export default function ZoneEditor({ nameServer }: ZoneEditorProps) {
  const [availableZones, setAvailableZones] = useState<string[]>([]);
  const [selectedZone, setSelectedZone] = useState<string>("");

  const [isCreating, setIsCreating] = useState(false);
  const [newZoneName, setNewZoneName] = useState("");

  const [origin, setOrigin] = useState("");
  const [defaultTtl, setDefaultTtl] = useState(86400);
  const [records, setRecords] = useState<DnsRecord[]>([]);

  const [isLoading, setIsLoading] = useState(false);
  const [saveStatus, setSaveStatus] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");

  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);

  // 1. Fetch File List
  useEffect(() => {
    if (nameServer === "API" || nameServer === "Resolver") return;

    let isCurrent = true; // Safety flag!

    // Instantly wipe the UI when the tab changes so old data doesn't bleed over
    setAvailableZones([]);
    setSelectedZone("");
    setRecords([]);

    const loadFileList = async () => {
      const list = await window.electron.fetchZoneList(nameServer);

      if (!isCurrent) return; // If they clicked another tab while this was loading, ABORT!

      setAvailableZones(list);
      if (list.length > 0) {
        setSelectedZone(list[0]);
      } else {
        setSelectedZone("");
        setRecords([]);
      }
    };
    loadFileList();

    // Cleanup function: runs the millisecond the nameServer (tab) changes
    return () => {
      isCurrent = false;
    };
  }, [nameServer]);

  // 2. Fetch Zone Data
  useEffect(() => {
    if (!selectedZone) return;

    let isCurrent = true; // Safety flag!

    const loadData = async () => {
      setIsLoading(true);
      const data = await window.electron.fetchZoneData(
        nameServer,
        selectedZone,
      );

      if (!isCurrent) return; // If they switched tabs during the fetch, ABORT!

      if (data) {
        setOrigin(data.origin);
        setDefaultTtl(data.defaultTtl);
        setRecords(data.records);
      } else {
        // If FastAPI returns null (or 404), ensure the table clears
        setRecords([]);
      }
      setIsLoading(false);
    };
    loadData();

    // Cleanup function
    return () => {
      isCurrent = false;
    };
  }, [nameServer, selectedZone]);

  // 3. Create New Zone
  const handleCreateNewZone = async () => {
    if (!newZoneName.trim()) return;
    const cleanName = newZoneName.trim().replace(/\.$/, "");

    try {
      setSaveStatus("saving");
      // Use the newly added backend IPC route!
      const result = await window.electron.createNewZone(nameServer, cleanName);

      if (result.success) {
        setAvailableZones((prev) => [...prev, cleanName]);
        setSelectedZone(cleanName);
        setIsCreating(false);
        setNewZoneName("");
        setSaveStatus("saved");
      } else {
        console.error("Creation failed:", result.error);
        setSaveStatus("error");
      }
    } catch (e) {
      if (e instanceof Error) setSaveStatus("error");
    } finally {
      setTimeout(() => setSaveStatus("idle"), 2500);
    }
  };

  const handleDeleteZone = async (zoneName: string) => {
    try {
      const result = await window.electron.deleteZone(nameServer, zoneName);

      if (result.success) {
        // 1. Filter the deleted zone out of our local state array
        const updatedZones = availableZones.filter((zone) => zone !== zoneName);
        setAvailableZones(updatedZones);

        // 2. Decide what to show the user next
        if (updatedZones.length > 0) {
          // Auto-select the first remaining zone.
          setSelectedZone(updatedZones[0]);
        } else {
          // If they deleted the very last file in the folder, clear the board completely
          setSelectedZone("");
          setRecords([]);
          setOrigin("");
          setDefaultTtl(86400);
        }
      } else {
        console.error("Deletion failed:", result.error);
      }
    } catch (error) {
      if (error instanceof Error) {
        console.error("Unexpected Error occurred (handleDeleteZone):", error);
      }
    }
  };
  const handleAddRecord = () => {
    setRecords((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        name: "",
        class: "IN",
        type: "A",
        ttl: undefined,
        data: "",
      },
    ]);
  };

  const handleUpdateRecord: UpdateRecordFn = (id, field, value) => {
    setRecords((prev) =>
      prev.map((r) => (r.id === id ? { ...r, [field]: value } : r)),
    );
  };

  const handleDeleteRecord = (id: string) => {
    setRecords((prev) => prev.filter((r) => r.id !== id));
  };

  const handleSave = async () => {
    setSaveStatus("saving");
    try {
      const payload: ZoneData = { origin, defaultTtl, records };
      const result = await window.electron.saveZoneData(
        nameServer,
        selectedZone,
        payload,
      );
      setSaveStatus(result.success ? "saved" : "error");
    } catch (error) {
      if (error instanceof Error) setSaveStatus("error");
    } finally {
      setTimeout(() => setSaveStatus("idle"), 2500);
    }
  };

  if (nameServer === "API" || nameServer === "Resolver") {
    return (
      <div className="zone-empty-state">
        <h2>{nameServer}</h2>
        <p>This component does not manage a static zone file.</p>
      </div>
    );
  }

  return (
    <div className="zone-editor-container">
      <ZoneSelection
        serverName={nameServer}
        availableZones={availableZones}
        selectedZone={selectedZone}
        origin={origin}
        defaultTtl={defaultTtl}
        isCreating={isCreating}
        newZoneName={newZoneName}
        onSelectZone={setSelectedZone}
        onChangeOrigin={setOrigin}
        onChangeTtl={setDefaultTtl}
        onSetIsCreating={setIsCreating}
        onSetNewZoneName={setNewZoneName}
        onCreateZone={handleCreateNewZone}
        onDeleteZone={() => setIsDeleteModalOpen(true)}
      />

      {isLoading ? (
        <p>Loading zone data from backend...</p>
      ) : availableZones.length === 0 && !isCreating ? (
        <p>No zone files found. Click "+ New Zone" to create one.</p>
      ) : !isCreating ? (
        <>
          <ZoneTable
            records={records}
            onUpdateRecord={handleUpdateRecord}
            onDeleteRecord={handleDeleteRecord}
          />
          <ZoneFunctions
            isLoading={isLoading}
            saveStatus={saveStatus}
            onAddRecord={handleAddRecord}
            onSave={handleSave}
          />
          <ConfirmModal
            isOpen={isDeleteModalOpen}
            title="Confirm Deletion"
            message={`Are you absolutely sure you want to delete ${selectedZone}.zone? This cannot be undone.`}
            onConfirm={() => {
              setIsDeleteModalOpen(false); // Close the modal
              handleDeleteZone(selectedZone); // Execute the actual delete
            }}
            onCancel={() => setIsDeleteModalOpen(false)}
          />
        </>
      ) : null}
    </div>
  );
}
