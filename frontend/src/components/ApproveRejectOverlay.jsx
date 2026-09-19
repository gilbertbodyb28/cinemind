import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Library, Loader2, Star } from "lucide-react";
import AddToLibraryDialog from "@/components/AddToLibraryDialog";
import { toast } from "sonner";
import { api } from "@/lib/api";

export const APPROVE_ICON = "/icons/approve.svg";
export const REJECT_ICON = "/icons/reject.svg";

/** Max five posters per row; fewer columns on smaller screens so each card stays large. */
export const POSTER_GRID =
  "grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 gap-5 lg:gap-6";

/** Same footprint as the Send to MediaManager postcard. */
export const POSTER_STAT =
  "glass-strong rounded-2xl px-2.5 py-2 flex items-center gap-2 text-left w-[11rem] min-h-[3.85rem] shrink-0";

export function PosterStat({ icon, title, subtitle, className = "" }) {
  return (
    <span className={`${POSTER_STAT} ${className}`}>
      <span className="w-8 h-8 rounded-xl glass grid place-items-center shrink-0">
        {icon}
      </span>
      <span className="min-w-0">
        <span className="block text-[11px] font-medium leading-tight text-[#F6EFE4] line-clamp-2">{title}</span>
        <span className="block text-[9px] font-mono uppercase tracking-wider text-[#8C7F6D] mt-0.5">{subtitle}</span>
      </span>
    </span>
  );
}

export function MatchStat({ score, className = "", subtitle = "match" }) {
  const n = Number.isFinite(Number(score)) ? Math.round(Number(score)) : 0;
  return (
    <PosterStat
      icon={<span className="font-mono text-[10px] font-bold text-[#D8B26A]">{n}</span>}
      title={`${n}% match`}
      subtitle={subtitle}
      className={className}
    />
  );
}

export function RatingStat({ rating }) {
  if (rating == null || rating === "") return null;
  return (
    <PosterStat
      icon={<Star className="w-4 h-4 fill-current text-[#D8B26A]" />}
      title={String(rating)}
      subtitle="rating"
    />
  );
}

export function StatusStat({ status, className = "" }) {
  const raw = String(status || "unknown");
  const [head, ...rest] = raw.split(/[_\s]+/);
  const title = head ? head.charAt(0).toUpperCase() + head.slice(1) : "Unknown";
  const subtitle = rest.length ? rest.join(" ") : "status";
  return (
    <PosterStat
      icon={<span className="font-mono text-[9px] font-bold text-[#D8B26A]">{title.slice(0, 2).toUpperCase()}</span>}
      title={title}
      subtitle={subtitle}
      className={className}
    />
  );
}

export function mediaManagerLibraryLabel(item) {
  const kind = String(item?.type || item?.media_type || "movie").toLowerCase();
  if (["show", "tv", "series", "anime"].includes(kind)) return "TV";
  return "Movies";
}

export function sendToast(title, data, item) {
  const dest =
    data?.media_type === "show" || data?.media_type === "tv"
      ? "TV"
      : data?.media_type === "movie"
        ? "Movies"
        : mediaManagerLibraryLabel(item);
  toast.success(
    data?.already_existed
      ? `${title} was already in MediaManager ${dest}`
      : `${title} sent to MediaManager ${dest}`,
  );
}

export async function sendToMediaManager(item, { requestId, options } = {}) {
  const looksLikeRequest = (value) => String(value || "").startsWith("req_");
  const reqId = requestId || item?.request_id || (looksLikeRequest(item?.id) ? item.id : null);
  const recId = item?.recommendation_id || (looksLikeRequest(item?.id) ? null : item?.id);

  if (reqId) {
    try {
      const r = await api.post(`/requests/${reqId}/approve`, options || {});
      sendToast(item?.title, r.data, item);
      return r.data;
    } catch (error) {
      if (error?.status !== 404) throw error;
    }
  }
  if (recId && recId !== reqId) {
    const r = await api.post(`/recommendations/${recId}/approve`, options || {});
    sendToast(item?.title, r.data, item);
    return r.data;
  }
  throw new Error("Could not send to MediaManager");
}

export function SendToLibraryButton({
  rec,
  requestId,
  onSend,
  onDone,
  testid,
  className = "",
}) {
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const dest = mediaManagerLibraryLabel(rec);
  const id = testid || `send-to-library-${requestId || rec?.id}`;

  const [dialogOpen, setDialogOpen] = useState(false);

  const open = (event) => {
    event?.stopPropagation();
    event?.preventDefault();
    if (busy) return;
    setDialogOpen(true);
  };

  const send = async (options) => {
    setBusy(true);
    try {
      if (onSend) {
        await onSend(options);
        setDialogOpen(false);
        onDone?.(rec?.id);
        return;
      }
      const data = await sendToMediaManager(rec, { requestId, options });
      setDialogOpen(false);
      onDone?.(requestId || rec?.id, data);
    } catch (error) {
      handleApproveError(error, navigate);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <AddToLibraryDialog
        open={dialogOpen}
        items={[rec]}
        busy={busy}
        onCancel={() => setDialogOpen(false)}
        onConfirm={send}
      />
    <button
      data-testid={id}
      type="button"
      disabled={busy}
      onClick={open}
      title={`Send to MediaManager ${dest} library`}
      className={`${POSTER_STAT} ${className} hover:border-[rgba(216,178,106,0.5)] transition-colors disabled:opacity-80`}
    >
      <span className="w-8 h-8 rounded-xl glass grid place-items-center shrink-0">
        {busy ? (
          <Loader2 className="w-4 h-4 text-[#D8B26A] animate-spin" />
        ) : (
          <Library className="w-4 h-4 text-[#D8B26A]" />
        )}
      </span>
      <span className="min-w-0">
        <span className="block text-[11px] font-medium leading-tight text-[#F6EFE4] line-clamp-2">
          Send to MediaManager
        </span>
        <span className="block text-[9px] font-mono uppercase tracking-wider text-[#8C7F6D] mt-0.5">
          {dest} library
        </span>
      </span>
    </button>
    </>
  );
}

export function ActionIconButton({ testid, label, src, onClick, disabled = false, className = "" }) {
  return (
    <button
      data-testid={testid}
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={`w-[3.75rem] h-[3.75rem] sm:w-16 sm:h-16 bg-transparent p-0 m-0 border-0 outline-none shadow-none ring-0 transition-transform hover:scale-110 active:scale-95 disabled:opacity-40 disabled:hover:scale-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[rgba(216,178,106,0.55)] ${className}`}
    >
      <img
        src={src}
        alt=""
        draggable={false}
        className="block w-full h-full object-contain pointer-events-none select-none"
      />
    </button>
  );
}

export function RejectButton({ testid, onClick, className = "" }) {
  return (
    <ActionIconButton
      testid={testid}
      label="Reject"
      src={REJECT_ICON}
      onClick={onClick}
      className={className}
    />
  );
}

export default function ApproveRejectOverlay({ id, onApprove, onReject, disabled = false, className = "" }) {
  return (
    <div className={`absolute inset-x-0 bottom-0 z-10 px-3 pb-3 pt-16 pointer-events-none bg-gradient-to-t from-black/80 via-black/40 to-transparent ${className}`}>
      <div className="pointer-events-auto flex items-center justify-center gap-4">
        <ActionIconButton
          testid={`approve-request-${id}`}
          label="Approve"
          src={APPROVE_ICON}
          onClick={onApprove}
          disabled={disabled}
        />
        <ActionIconButton
          testid={`reject-request-${id}`}
          label="Reject"
          src={REJECT_ICON}
          onClick={onReject}
          disabled={disabled}
        />
      </div>
    </div>
  );
}

export async function approveRecommendation(rec) {
  return sendToMediaManager(rec);
}

export async function rejectRecommendation(rec) {
  if (rec.request_id) {
    try {
      await api.post(`/requests/${rec.request_id}/reject`);
      toast("Rejected");
      return;
    } catch {
      /* fall through to dismiss the pick */
    }
  }
  await api.post(`/recommendations/${rec.id}/dismiss`);
  toast("Rejected");
}

export function handleApproveError(error, navigate) {
  const status = error?.status ?? error?.response?.status;
  if (status === 409) {
    toast.error("Connect MediaManager before approving", {
      action: {
        label: "Connect",
        onClick: () => navigate?.("/connections"),
      },
    });
    return;
  }
  toast.error(error?.message || error?.response?.data?.detail || "Could not send to MediaManager");
}

export function RecPosterActions({ rec, onApproved, onRejected, testidPrefix }) {
  const navigate = useNavigate();
  const id = testidPrefix || rec.id;
  const [busy, setBusy] = useState(false);
  const done = !!rec.in_library || rec?.status === "approved";

  const [dialogOpen, setDialogOpen] = useState(false);

  const openDialog = (event) => {
    event?.stopPropagation();
    event?.preventDefault();
    if (done || busy) return;
    setDialogOpen(true);
  };

  const push = async (options) => {
    setBusy(true);
    try {
      const data = await sendToMediaManager(rec, { requestId: rec.request_id, options });
      setDialogOpen(false);
      onApproved?.(rec.id, data);
    } catch (error) {
      handleApproveError(error, navigate);
    } finally {
      setBusy(false);
    }
  };

  const reject = async (event) => {
    event?.stopPropagation();
    event?.preventDefault();
    try {
      await rejectRecommendation(rec);
      onRejected?.(rec.id);
    } catch (error) {
      toast.error(error?.message || "Could not reject");
    }
  };

  return (
    <>
      <AddToLibraryDialog
        open={dialogOpen}
        items={[rec]}
        busy={busy}
        onCancel={() => setDialogOpen(false)}
        onConfirm={push}
      />
      <div className="absolute top-3 right-3 z-30 pointer-events-auto">
        <SendToLibraryButton rec={rec} requestId={rec.request_id} onDone={onApproved} />
      </div>
      {done ? null : (
        <div className="absolute inset-x-0 bottom-0 z-10 px-3 pb-3 pt-16 pointer-events-none bg-gradient-to-t from-black/80 via-black/40 to-transparent">
          <div className="pointer-events-auto flex items-center justify-center gap-4">
            <ActionIconButton
              testid={`approve-button-${id}-poster`}
              label="Approve"
              src={APPROVE_ICON}
              onClick={openDialog}
              disabled={busy}
            />
            {onRejected ? (
              <ActionIconButton
                testid={`reject-request-${id}`}
                label="Reject"
                src={REJECT_ICON}
                onClick={reject}
              />
            ) : null}
          </div>
        </div>
      )}
    </>
  );
}
