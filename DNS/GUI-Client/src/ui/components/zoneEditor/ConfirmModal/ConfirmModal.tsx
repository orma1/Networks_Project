import React from "react";
import "../ZoneEditor.css";
import "./ConfirmModal.css";
interface ConfirmModalProps {
  isOpen: boolean;
  title: string;
  message: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmModal({
  isOpen,
  title,
  message,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content">
        <h3>{title}</h3>
        <p>{message}</p>

        <div className="modal-actions">
          <button className="btn-cancel" onClick={onCancel}>
            Cancel
          </button>

          {/* We keep your global btn-delete class here since it already works! */}
          <button className="btn-delete" onClick={onConfirm}>
            Delete
          </button>
        </div>
      </div>
    </div>
  );
}
