import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AuthLayout } from "../components/auth/AuthLayout";
import { Input } from "../components/ui/Input";
import { Button } from "../components/ui/Button";
import { useAuth } from "../lib/auth";
import { apiError } from "../lib/api";
import { GoogleButton, OrDivider } from "../components/auth/GoogleButton";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(email, password);
      navigate("/dashboard");
    } catch (err) {
      const data = (err as { response?: { data?: { code?: string } } })?.response?.data;
      if (data?.code === "email_unverified") {
        navigate(`/verify-otp?email=${encodeURIComponent(email)}`);
        return;
      }
      setError(apiError(err, "Login failed. Check your email and password."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout
      title="Welcome back"
      subtitle="Log in to your JobCatch account"
      footer={<>Don&rsquo;t have an account? <Link to="/register" className="font-semibold text-brand-600">Create one</Link></>}
    >
      <div className="mb-4 space-y-4">
        <GoogleButton text="continue_with" />
        <OrDivider />
      </div>
      <form onSubmit={submit} className="space-y-4">
        {error && <div className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600">{error}</div>}
        <Input label="Email" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.com" />
        <Input label="Password" type="password" required value={password} onChange={(e) => setPassword(e.target.value)} placeholder="••••••••" />
        <div className="flex justify-end">
          <Link to="/forgot-password" className="text-sm font-medium text-brand-600 hover:underline">Forgot password?</Link>
        </div>
        <Button type="submit" disabled={busy} className="w-full">{busy ? "Logging in…" : "Log in"}</Button>
      </form>
    </AuthLayout>
  );
}
