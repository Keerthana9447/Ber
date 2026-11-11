import { createContext, forwardRef, useCallback, useContext, useEffect, useRef, useState, type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Inbox, Loader2, PlugZap, X } from "lucide-react";
import { cn } from "../lib/cn";

// ---------------------------------------------------------------- Button
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
  loading?: boolean;
};
export function Button({ variant = "primary", loading, disabled, className, children, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-forest-700 text-white hover:bg-forest-800 shadow-card",
    secondary: "bg-white text-forest-800 border border-ivory-300 hover:bg-ivory-100",
    ghost: "text-forest-800 hover:bg-forest-50",
    danger: "bg-red-700 text-white hover:bg-red-800",
  }[variant];
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-60",
        styles,
        className,
      )}
    >
      {loading && <Loader2 className="h-4 w-4 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

// ---------------------------------------------------------------- Input
type InputProps = InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string };
export const Field = forwardRef<HTMLInputElement, InputProps>(function Field({ label, error, id, className, ...rest }, ref) {
  const inputId = id ?? `f-${label.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <div className={className}>
      <label htmlFor={inputId} className="mb-1 block text-sm font-medium text-slate-700">
        {label}
      </label>
      <input
        id={inputId}
        ref={ref}
        aria-invalid={!!error}
        aria-describedby={error ? `${inputId}-err` : undefined}
        {...rest}
        className={cn(
          "w-full rounded-lg border bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400",
          error ? "border-red-500" : "border-ivory-300",
        )}
      />
      {error && (
        <p id={`${inputId}-err`} role="alert" className="mt-1 text-xs text-red-700">
          {error}
        </p>
      )}
    </div>
  );
});

// ---------------------------------------------------------------- Card, Badge
export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <section className={cn("rounded-xl border border-ivory-300 bg-white shadow-card", className)}>{children}</section>;
}

export function Badge({ children }: { children: ReactNode }) {
  const text = String(children ?? "");
  const key = text.toLowerCase();
  let tone = "bg-slate-100 text-slate-700 border-slate-200";
  if (/inactive/.test(key)) tone = "bg-slate-100 text-slate-700 border-slate-200";
  else if (/overdue|cancel|reject|suspend|terminat|reversed/.test(key)) tone = "bg-red-50 text-red-800 border-red-200";
  else if (/draft|pending|unpaid|partial|sent|ordered|requested/.test(key)) tone = "bg-gold-300/30 text-gold-600 border-gold-300";
  else if (/paid|active|issued|approved|posted|delivered|received|accepted|confirmed|^in$/.test(key)) tone = "bg-forest-50 text-forest-800 border-forest-200";
  return (
    <span className={cn("inline-block rounded-full border px-2 py-0.5 text-xs font-medium capitalize", tone)}>
      {text.replace(/_/g, " ") || "—"}
    </span>
  );
}

// ---------------------------------------------------------------- States
export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded bg-ivory-200", className)} aria-hidden />;
}

export function LoadingState({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center justify-center gap-2 py-16 text-sm text-slate-500">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> {label}
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <Inbox className="h-8 w-8 text-forest-300" aria-hidden />
      <p className="font-medium text-slate-700">{title}</p>
      {hint && <p className="max-w-sm text-sm text-slate-500">{hint}</p>}
    </div>
  );
}

export function ErrorState({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-col items-center gap-3 py-16 text-center">
      <AlertTriangle className="h-8 w-8 text-red-600" aria-hidden />
      <p className="max-w-md text-sm text-slate-700">{message}</p>
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}

/** Shown when the backend has no route for this module yet. No data is invented. */
export function NotAvailableState({ endpoint, label }: { endpoint: string; label: string }) {
  return (
    <div className="flex flex-col items-center gap-2 py-16 text-center">
      <PlugZap className="h-8 w-8 text-gold-500" aria-hidden />
      <p className="font-medium text-slate-800">{label} is not available yet</p>
      <p className="max-w-md text-sm text-slate-500">
        The backend does not expose <code className="rounded bg-ivory-200 px-1">GET /api/v1{endpoint}</code> yet. This screen will
        fill in automatically once that endpoint exists (see <code className="rounded bg-ivory-200 px-1">docs/API_CONTRACT.md</code>).
      </p>
    </div>
  );
}

// ---------------------------------------------------------------- Modal / confirm
export function Modal({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center bg-forest-950/50 p-4 pt-[12vh]" onMouseDown={onClose}>
      <div
        ref={ref}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(e) => e.stopPropagation()}
        className="w-full max-w-lg rounded-xl bg-white shadow-xl"
      >
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open, title, message, confirmLabel = "Confirm", onConfirm, onCancel, danger, children,
}: {
  open: boolean; title: string; message: string; confirmLabel?: string; danger?: boolean;
  onConfirm: () => void | Promise<void>; onCancel: () => void;
  children?: ReactNode;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <div className="p-6">
        <h2 className="text-lg font-semibold text-slate-900">{title}</h2>
        <p className="mt-2 text-sm text-slate-600">{message}</p>
        {children && <div className="mt-4">{children}</div>}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="secondary" onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant={danger ? "danger" : "primary"}
            loading={busy}
            onClick={async () => {
              setBusy(true);
              try {
                await onConfirm();
              } finally {
                setBusy(false);
              }
            }}
          >
            {confirmLabel}
          </Button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- Toasts
type Toast = { id: number; kind: "success" | "error"; message: string };
const ToastContext = createContext<(kind: Toast["kind"], message: string) => void>(() => undefined);
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], message: string) => {
    const id = Date.now() + Math.random();
    setToasts((t) => [...t, { id, kind, message }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 5000);
  }, []);
  return (
    <ToastContext.Provider value={push}>
      {children}
      <div className="fixed bottom-4 right-4 z-[60] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2" aria-live="polite">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              "flex items-start gap-2 rounded-lg border bg-white p-3 text-sm shadow-lg",
              t.kind === "success" ? "border-forest-300" : "border-red-300",
            )}
          >
            {t.kind === "success" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-forest-600" aria-hidden />
            ) : (
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" aria-hidden />
            )}
            <span className="flex-1 text-slate-800">{t.message}</span>
            <button aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))}>
              <X className="h-4 w-4 text-slate-400" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
