import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { Button, Card, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";

type Settings = {
  id: number;
  legal_name: string;
  trade_name: string | null;
  address: string | null;
  phone: string | null;
  email: string | null;
  gstin: string | null;
  pan: string | null;
  state_code: string | null;
  bank_name: string | null;
  bank_account_no: string | null;
  bank_ifsc: string | null;
  upi_id: string | null;
  logo_path: string | null;
  invoice_prefix: string;
  fy_start_month: number;
  allow_invoice_issue_without_approval: boolean;
  terms_and_conditions: string | null;
  authorized_signatory: string | null;
  updated_at: string | null;
};
type State = { code: string; name: string };

export const settingsFormSchema = z.object({
  legal_name: z.string().trim().min(1, "Legal name is required").max(255),
  trade_name: z.string().max(255),
  address: z.string().max(2000),
  phone: z.string().max(64),
  email: z.string().max(320).refine((value) => !value || z.email().safeParse(value).success, "Enter a valid email address"),
  gstin: z.string().trim().toUpperCase().refine((value) => !value || /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][A-Z0-9]Z[A-Z0-9]$/.test(value), "Enter a valid GSTIN format"),
  pan: z.string().trim().toUpperCase().refine((value) => !value || /^[A-Z]{5}[0-9]{4}[A-Z]$/.test(value), "Enter a valid PAN format"),
  state_code: z.string(),
  bank_name: z.string().max(255),
  bank_account_no: z.string().max(128),
  bank_ifsc: z.string().trim().toUpperCase().refine((value) => !value || /^[A-Z]{4}0[A-Z0-9]{6}$/.test(value), "Enter a valid IFSC format"),
  upi_id: z.string().max(255),
  logo_path: z.string().max(1000),
  invoice_prefix: z.string().trim().regex(/^[A-Za-z0-9/-]{1,4}$/, "Use 1–4 letters, digits, hyphens or slashes to keep invoice numbers within 16 characters"),
  fy_start_month: z.string().regex(/^(?:[1-9]|1[0-2])$/, "Select a month from 1 to 12"),
  allow_invoice_issue_without_approval: z.boolean(),
  terms_and_conditions: z.string().max(5000),
  authorized_signatory: z.string().max(255),
}).superRefine((values, context) => {
  if (values.gstin && values.state_code && values.gstin.slice(0, 2) !== values.state_code) {
    context.addIssue({ code: "custom", path: ["gstin"], message: "GSTIN state prefix must match the selected state" });
  }
  if (values.gstin && !values.state_code) {
    context.addIssue({ code: "custom", path: ["state_code"], message: "Select a state when entering a GSTIN" });
  }
});

export type SettingsFormValues = z.infer<typeof settingsFormSchema>;

const blankSettings: SettingsFormValues = {
  legal_name: "",
  trade_name: "",
  address: "",
  phone: "",
  email: "",
  gstin: "",
  pan: "",
  state_code: "",
  bank_name: "",
  bank_account_no: "",
  bank_ifsc: "",
  upi_id: "",
  logo_path: "",
  invoice_prefix: "",
  fy_start_month: "4",
  allow_invoice_issue_without_approval: false,
  terms_and_conditions: "",
  authorized_signatory: "",
};

function toForm(settings: Settings): SettingsFormValues {
  return {
    legal_name: settings.legal_name,
    trade_name: settings.trade_name ?? "",
    address: settings.address ?? "",
    phone: settings.phone ?? "",
    email: settings.email ?? "",
    gstin: settings.gstin ?? "",
    pan: settings.pan ?? "",
    state_code: settings.state_code ?? "",
    bank_name: settings.bank_name ?? "",
    bank_account_no: settings.bank_account_no ?? "",
    bank_ifsc: settings.bank_ifsc ?? "",
    upi_id: settings.upi_id ?? "",
    logo_path: settings.logo_path ?? "",
    invoice_prefix: settings.invoice_prefix,
    fy_start_month: String(settings.fy_start_month),
    allow_invoice_issue_without_approval: settings.allow_invoice_issue_without_approval,
    terms_and_conditions: settings.terms_and_conditions ?? "",
    authorized_signatory: settings.authorized_signatory ?? "",
  };
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const canUpdate = can("settings:update");
  const query = useQuery({
    queryKey: ["settings"],
    queryFn: ({ signal }) => api<Settings>("/settings", { signal }),
    retry: false,
  });
  const states = useQuery({
    queryKey: ["states"],
    queryFn: ({ signal }) => api<State[]>("/states", { signal }),
    retry: false,
  });
  const defaults = useMemo(() => query.data ? toForm(query.data) : blankSettings, [query.data]);
  const form = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsFormSchema),
    values: defaults,
  });
  const save = useMutation({
    mutationFn: (values: SettingsFormValues) =>
      api<Settings>("/settings", {
        method: "PUT",
        body: Object.fromEntries(
          Object.entries(values).map(([key, value]) => [
            key,
            key === "fy_start_month" ? Number(value) : typeof value === "string" && value === "" ? null : value,
          ]),
        ),
      }),
    onSuccess: async (settings) => {
      queryClient.setQueryData(["settings"], settings);
      await queryClient.invalidateQueries({ queryKey: ["settings", "readiness"] });
      toast("success", "Company settings saved.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not save company settings."),
  });

  useEffect(() => {
    if (query.data) form.reset(toForm(query.data));
  }, [query.data, form]);

  const submit = form.handleSubmit((values) => save.mutate(values));
  const fields = form.formState.errors;

  return (
    <div className="mx-auto max-w-4xl space-y-4">
      <div>
        <h1 className="text-2xl font-semibold text-forest-900">Company settings</h1>
        <p className="text-sm text-slate-500">Enter official company and tax details supplied by your administrator. No company identifiers are prefilled.</p>
      </div>
      <Card className="p-6">
        {query.isLoading && <LoadingState />}
        {query.isError && <ErrorState message={(query.error as Error).message} onRetry={() => query.refetch()} />}
        {states.isError && <ErrorState message={(states.error as Error).message} onRetry={() => states.refetch()} />}
        {query.isSuccess && states.isSuccess && (
          <form onSubmit={submit} className="space-y-6">
            {!canUpdate && <p role="alert" className="text-sm text-red-700">Your role can view settings but cannot update them.</p>}
            <section>
              <h2 className="text-lg font-medium text-slate-900">Company identity</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Legal name" {...form.register("legal_name")} error={fields.legal_name?.message} required />
                <Field label="Trade name" {...form.register("trade_name")} error={fields.trade_name?.message} />
                <label className="block text-sm font-medium text-slate-700">
                  State
                  <select {...form.register("state_code")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                    <option value="">Select state</option>
                    {states.data.map((state) => <option key={state.code} value={state.code}>{state.code} · {state.name}</option>)}
                  </select>
                  {fields.state_code?.message && <span role="alert" className="mt-1 block text-xs text-red-700">{fields.state_code.message}</span>}
                </label>
                <Field label="Phone" {...form.register("phone")} error={fields.phone?.message} />
                <Field label="Email" type="email" {...form.register("email")} error={fields.email?.message} />
                <Field label="GSTIN" {...form.register("gstin")} error={fields.gstin?.message} />
                <Field label="PAN" {...form.register("pan")} error={fields.pan?.message} />
                <Field label="Logo path" {...form.register("logo_path")} error={fields.logo_path?.message} />
                <label className="text-sm font-medium text-slate-700 sm:col-span-2">
                  Registered address
                  <textarea {...form.register("address")} rows={3} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
                  {fields.address?.message && <span role="alert" className="mt-1 block text-xs text-red-700">{fields.address.message}</span>}
                </label>
              </div>
            </section>
            <section className="border-t border-ivory-200 pt-5">
              <h2 className="text-lg font-medium text-slate-900">Invoice configuration</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Invoice prefix" {...form.register("invoice_prefix")} error={fields.invoice_prefix?.message} required maxLength={4} />
                <label className="block text-sm font-medium text-slate-700">
                  Financial year start month
                  <select {...form.register("fy_start_month")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
                    {Array.from({ length: 12 }, (_, i) => <option key={i + 1} value={i + 1}>{new Date(2026, i, 1).toLocaleString("en", { month: "long" })}</option>)}
                  </select>
                </label>
                <label className="flex items-start gap-2 text-sm text-slate-700 sm:col-span-2">
                  <input type="checkbox" {...form.register("allow_invoice_issue_without_approval")} className="mt-1" />
                  <span>Allow eligible users with both approval and issue permissions to issue drafts without a separate approval (default off).</span>
                </label>
                <Field label="Authorized signatory" {...form.register("authorized_signatory")} error={fields.authorized_signatory?.message} />
                <label className="text-sm font-medium text-slate-700 sm:col-span-2">
                  Terms and conditions
                  <textarea {...form.register("terms_and_conditions")} rows={3} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
                </label>
              </div>
            </section>
            <section className="border-t border-ivory-200 pt-5">
              <h2 className="text-lg font-medium text-slate-900">Bank details</h2>
              <div className="mt-4 grid gap-4 sm:grid-cols-2">
                <Field label="Bank name" {...form.register("bank_name")} error={fields.bank_name?.message} />
                <Field label="Account number" {...form.register("bank_account_no")} error={fields.bank_account_no?.message} />
                <Field label="IFSC" {...form.register("bank_ifsc")} error={fields.bank_ifsc?.message} />
                <Field label="UPI ID" {...form.register("upi_id")} error={fields.upi_id?.message} />
              </div>
            </section>
            <div className="flex items-center justify-between border-t border-ivory-200 pt-4">
              <p role="status" className="text-sm text-forest-700">{save.isSuccess ? "Settings saved." : ""}</p>
              <Button type="submit" loading={save.isPending} disabled={!canUpdate || !form.formState.isDirty}>
                Save settings
              </Button>
            </div>
          </form>
        )}
      </Card>
    </div>
  );
}
