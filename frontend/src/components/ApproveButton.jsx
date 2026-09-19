import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { ActionIconButton, APPROVE_ICON, handleApproveError, sendToMediaManager } from "@/components/ApproveRejectOverlay";

export default function ApproveButton({ rec, onDone, variant = "icon", className = "" }) {
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const done = !!rec.in_library;
  const testid = `approve-button-${rec.id}${variant === "pill" ? "-modal" : variant === "poster" ? "-poster" : ""}`;

  const push = async (e) => {
    e?.stopPropagation();
    if (done || busy) return;
    setBusy(true);
    try {
      const data = await sendToMediaManager(rec);
      onDone?.(rec.id, data);
    } catch (err) {
      handleApproveError(err, navigate);
    } finally {
      setBusy(false);
    }
  };

  if (done) return null;

  return (
    <ActionIconButton
      testid={testid}
      label="Approve"
      src={APPROVE_ICON}
      onClick={push}
      disabled={busy}
      className={className}
    />
  );
}
