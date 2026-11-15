import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { apiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { GOOGLE_CLIENT_ID } from "../../lib/config";

interface GoogleId {
  initialize: (o: { client_id: string; callback: (r: { credential: string }) => void; ux_mode?: string; auto_select?: boolean }) => void;
  renderButton: (el: HTMLElement, o: Record<string, unknown>) => void;
}
declare global { interface Window { google?: { accounts: { id: GoogleId } } } }

const SCRIPT = "https://accounts.google.com/gsi/client";
let loading: Promise<void> | null = null;

function loadGsi() {
  if (window.google?.accounts?.id) return Promise.resolve();
  loading ??= new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT; s.async = true; s.defer = true;
    s.onload = () => resolve();
    s.onerror = () => { loading = null; reject(new Error("Google sign-in could not load.")); };
    document.head.appendChild(s);
  });
  return loading;
}

/** "Continue with Google" — signs in an existing account or creates one, then goes to the role hub. */
export function GoogleButton({ text = "continue_with" }: { text?: "continue_with" | "signup_with" | "signin_with" }) {
  const ref = useRef<HTMLDivElement>(null);
  const { loginWithGoogle } = useAuth();
  const navigate = useNavigate();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Latest sign-in handler, so Google's button is set up once (not on every render).
  const onCredential = useRef<(c: string) => void>(() => {});
  useEffect(() => {
    onCredential.current = async (credential: string) => {
      setError(""); setBusy(true);
      try {
        await loginWithGoogle(credential);
        navigate("/dashboard");
      } catch (err) {
        setError(apiError(err, "Google sign-in failed. Please try again."));
      } finally {
        setBusy(false);
      }
    };
  }, [loginWithGoogle, navigate]);

  useEffect(() => {
    let cancelled = false;
    loadGsi().then(() => {
      if (cancelled || !ref.current || !window.google) return;
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: ({ credential }) => onCredential.current(credential),
      });
      window.google.accounts.id.renderButton(ref.current, {
        type: "standard", theme: "outline", size: "large", shape: "pill", text,
        logo_alignment: "center", width: Math.min(ref.current.offsetWidth || 360, 400),
      });
    }).catch((e: Error) => !cancelled && setError(e.message));
    return () => { cancelled = true; };
  }, [text]);

  return (
    <div className="space-y-2">
      <div ref={ref} className="flex min-h-[44px] w-full justify-center" aria-busy={busy} />
      {busy && <p className="text-center text-xs text-ink/50">Signing you in…</p>}
      {error && <p className="rounded-lg bg-red-50 px-3 py-2 text-center text-sm text-red-600">{error}</p>}
    </div>
  );
}

export function OrDivider() {
  return (
    <div className="flex items-center gap-3 text-xs font-medium uppercase tracking-wider text-ink/40">
      <span className="h-px flex-1 bg-brand-100" />or<span className="h-px flex-1 bg-brand-100" />
    </div>
  );
}
