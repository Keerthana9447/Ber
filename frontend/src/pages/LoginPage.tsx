import { useState } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { ShieldCheck } from "lucide-react";
import { useAuth } from "../lib/auth";
import { ApiError } from "../lib/api";
import { Button, Field } from "../components/ui";

const schema = z.object({
  email: z.string().trim().min(1, "Email is required").email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});
type FormValues = z.infer<typeof schema>;

export function LoginPage() {
  const { status, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [formError, setFormError] = useState<string | null>(null);
  const { register, handleSubmit, formState } = useForm<FormValues>({ resolver: zodResolver(schema) });
  const from = (location.state as { from?: string } | null)?.from ?? "/";

  if (status === "authed") return <Navigate to={from} replace />;

  const onSubmit = handleSubmit(async (values) => {
    setFormError(null);
    try {
      await login(values.email, values.password);
      navigate(from, { replace: true });
    } catch (error) {
      setFormError(error instanceof ApiError ? error.message : "Sign-in failed. Please try again.");
    }
  });

  return (
    <div className="flex min-h-full items-center justify-center bg-gradient-to-br from-forest-900 to-forest-950 p-4">
      <div className="w-full max-w-md rounded-2xl bg-ivory-50 p-8 shadow-2xl">
        <div className="mb-6 text-center">
          <img
            src="/branding/berev-logo.png"
            alt="BEREV — Boundless EV Revolution"
            className="mx-auto mb-3 h-auto w-56 rounded bg-white object-contain"
          />
          <h1 className="text-xl font-semibold text-forest-900"></h1>
          <p className="text-sm text-slate-500">sign in to continue</p>
        </div>
        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <Field label="Email" type="email" autoComplete="username" autoFocus {...register("email")} error={formState.errors.email?.message} />
          <Field label="Password" type="password" autoComplete="current-password" {...register("password")} error={formState.errors.password?.message} />
          {formError && (
            <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800">
              {formError}
            </p>
          )}
          <Button type="submit" loading={formState.isSubmitting} className="w-full">
            Sign in
          </Button>
        </form>
        <p className="mt-6 flex items-center justify-center gap-1 text-xs text-slate-500">
          <ShieldCheck className="h-3.5 w-3.5 text-forest-600" /> Accounts are created by an administrator.
        </p>
      </div>
    </div>
  );
}
