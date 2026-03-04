interface ZoneFunctionsProps {
  isLoading: boolean;
  saveStatus: "idle" | "saving" | "saved" | "error";
  onAddRecord: () => void;
  onSave: () => void;
}

export default function ZoneFunctions({
  isLoading,
  saveStatus,
  onAddRecord,
  onSave,
}: ZoneFunctionsProps) {
  return (
    <div className="zone-func">
      <button className="btn-add" onClick={onAddRecord}>
        + Add Record
      </button>

      <button
        className="btn-save"
        onClick={onSave}
        disabled={isLoading || saveStatus !== "idle"}
        style={{
          backgroundColor:
            saveStatus === "saved"
              ? "#c7f9cc"
              : saveStatus === "error"
                ? "var(--status-off)"
                : undefined,
          color: saveStatus === "saved" ? "#000" : undefined,
          transition: "background-color 0.3s ease",
        }}
      >
        {saveStatus === "idle" && "Save Changes"}
        {saveStatus === "saving" && "Saving..."}
        {saveStatus === "saved" && "Saved!"}
        {saveStatus === "error" && "Error"}
      </button>
    </div>
  );
}
