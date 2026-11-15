/**
 * Origin resolution.
 *
 * Locally the frontend and API share an origin (Vite proxies /api and /media to
 * the backend), so relative paths just work. In production they're separate
 * domains — jobcatchonline.com and api.jobcatchonline.com — so anything that
 * points at the backend (media files, WebSockets) must be made absolute against
 * the API's origin, not the page's.
 */
const RAW_API_URL =
  (import.meta.env as Record<string, string | undefined>).VITE_API_URL ?? "/api/v1";

export const API_URL = RAW_API_URL;

/** Absolute origin of the API, or "" when it's same-origin (local dev). */
export const API_ORIGIN = (() => {
  if (!/^https?:\/\//i.test(RAW_API_URL)) return "";      // relative -> same origin
  try {
    return new URL(RAW_API_URL).origin;
  } catch {
    return "";
  }
})();

/** Absolute URL for a backend-served media file (photos, CVs, videos). */
export function mediaUrl(path: string | null | undefined): string {
  if (!path) return "";
  if (/^https?:\/\//i.test(path) || path.startsWith("blob:") || path.startsWith("data:")) {
    return path;                                           // already absolute
  }
  return `${API_ORIGIN}${path.startsWith("/") ? "" : "/"}${path}`;
}

/** ws:// or wss:// base for Channels, derived from the API origin. */
export function wsBase(): string {
  const origin = API_ORIGIN || window.location.origin;
  return origin.replace(/^http/i, "ws");
}

/**
 * The registered company that owns and operates JobCatch. Shown in the footer,
 * About, Contact and Terms pages (SMS sender-ID and payment providers check the
 * site for this). Fill in rcNumber / address to display them too.
 */
export const COMPANY = {
  brand: "JobCatch",
  legalName: "Spotlight Investments Limited",
  rcNumber: "9434426",
  address: "5 David Oladapo Street, Sangotedo, Lagos, Nigeria",
};

export const companyLine = () =>
  `${COMPANY.brand} is a product of ${COMPANY.legalName}${COMPANY.rcNumber ? ` (RC ${COMPANY.rcNumber})` : ""}.`;

/** Google OAuth Web client ID for "Continue with Google" (public, not a secret). */
export const GOOGLE_CLIENT_ID =
  (import.meta.env as Record<string, string | undefined>).VITE_GOOGLE_CLIENT_ID
  ?? "306763553702-jalc00es33k28etpj8shsufela5486t1.apps.googleusercontent.com";
