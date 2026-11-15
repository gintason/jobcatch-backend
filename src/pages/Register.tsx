import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthLayout } from "../components/auth/AuthLayout";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";
import { apiError } from "../lib/api";
import { GoogleButton, OrDivider } from "../components/auth/GoogleButton";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ full_name: "", email: "", phone: "", password: "", role: "customer" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await register(form);
      navigate(`/verify-otp?email=${encodeURIComponent(form.email)}`);
    } catch (err) {
      setError(apiError(err, "Registration failed. Please review your details."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Join JobCatch in under a minute"
      footer={<>Already have an account? <Link to="/login" className="font-semibold text-brand-600">Log in</Link></>}
    >
      <div className="mb-4 space-y-4">
        <GoogleButton text="signup_with" />
        <OrDivider />
      </div>
      <form onSubmit={submit} className="space-y-4">
        {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}
        <p className="rounded-xl bg-brand-50 px-3 py-2.5 text-xs leading-relaxed text-brand-700">
          One account covers everything on JobCatch. After verifying your email
          you&rsquo;ll choose how to continue &mdash; as a Customer, Artisan,
          Employer, or Job Seeker &mdash; and you can switch any time.
        </p>
        <Input label="Full name" required value={form.full_name} onChange={(e) => set("full_name", e.target.value)} placeholder="Amaka Obi" />
        <Input label="Email" type="email" required value={form.email} onChange={(e) => set("email", e.target.value)} placeholder="you@example.com" />
        <Input label="Phone (optional)" value={form.phone} onChange={(e) => set("phone", e.target.value)} placeholder="080 0000 0000" />
        <Input label="Password" type="password" required minLength={8} value={form.password} onChange={(e) => set("password", e.target.value)} placeholder="At least 8 characters" />
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Creating…" : "Create account"}</Button>
      </form>
    </AuthLayout>
  );
}
