import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, useToast } from "../components/ui";
import { InvoiceReadinessBanner } from "../components/InvoiceReadinessBanner";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { formatDate, formatINR } from "../lib/format";

type Customer = {
  id: number;
  code: string;
  name: string;
  state_code: string | null;
  gstin: string | null;
  billing_address: string | null;
  shipping_address: string | null;
  is_active: boolean;
};
type State = { code: string; name: string };
type Product = {
  id: number;
  sku: string;
  name: string;
  hsn_sac: string;
  unit: string;
  selling_price: string;
  gst_rate: string;
  cess_rate: string;
  is_active: boolean;
  is_service: boolean;
  is_serialized: boolean;
};
type Serial = { id: number; chassis_no: string | null; vin: string | null; motor_no: string | null; battery_serial: string | null };
type InvoiceListRow = {
  id: number;
  invoice_number: string | null;
  customer_id: number;
  customer_name: string;
  doc_type: string;
  invoice_date: string;
  due_date: string | null;
  status: "draft" | "issued";
  grand_total: string;
};
type InvoicePageData = { items: InvoiceListRow[]; total: number };
type InvoiceLineResponse = {
  id?: number;
  product_id: number | null;
  serial_id: number | null;
  description: string;
  hsn_sac: string;
  unit: string | null;
  quantity: string;
  unit_price: string;
  discount: string;
  taxable_value: string;
  gst_rate: string;
  cess_rate: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
};
type InvoiceDraft = {
  id: number;
  customer_id: number;
  place_of_supply_code: string;
  doc_type: "tax_invoice" | "bill_of_supply" | "proforma";
  reverse_charge: boolean;
  invoice_date: string;
  due_date: string | null;
  payment_terms: string | null;
  notes: string | null;
  status: string;
  lines: InvoiceLineResponse[];
};
type PreviewLine = {
  product_id: number | null;
  serial_id: number | null;
  description: string;
  hsn_sac: string;
  unit: string;
  quantity: string;
  unit_price: string;
  gross: string;
  discount: string;
  taxable_value: string;
  gst_rate: string;
  cess_rate: string;
  cgst: string;
  sgst: string;
  igst: string;
  cess: string;
  line_total: string;
};
export type Preview = {
  customer_id: number;
  doc_type: string;
  reverse_charge: boolean;
  seller_state_code: string;
  place_of_supply_code: string;
  is_inter_state: boolean;
  tax_applicable: boolean;
  lines: PreviewLine[];
  taxable_total: string;
  cgst_total: string;
  sgst_total: string;
  igst_total: string;
  cess_total: string;
  tax_total: string;
  total_before_round: string;
  round_off: string;
  grand_total: string;
  amount_in_words: string;
  hsn_summary: {
    hsn_sac: string;
    gst_rate: string;
    cess_rate: string;
    taxable: string;
    cgst: string;
    sgst: string;
    igst: string;
    cess: string;
  }[];
};

const lineSchema = z.object({
  product_id: z.string(),
  serial_id: z.string(),
  description: z.string(),
  hsn_sac: z.string(),
  unit: z.string(),
  quantity: z.string().regex(/^(?:0|[1-9]\d*)(?:\.\d{1,3})?$/, "Use a positive quantity with at most 3 decimal places").refine((value) => Number(value) > 0, "Quantity must be greater than zero"),
  unit_price: z.string().regex(/^\d+(?:\.\d{1,4})?$/, "Enter a non-negative unit price"),
  discount_mode: z.enum(["percent", "amount"]),
  discount_pct: z.string().regex(/^\d+(?:\.\d{1,2})?$/, "Enter a valid discount percentage").refine((value) => Number(value) <= 100, "Discount cannot exceed 100%"),
  discount_amount: z.string().regex(/^\d+(?:\.\d{1,2})?$/, "Enter a valid discount amount"),
  gst_rate: z.string().regex(/^\d+(?:\.\d{1,2})?$/, "Select an active GST rate"),
  cess_rate: z.string().regex(/^\d+(?:\.\d{1,2})?$/, "Enter a valid cess rate"),
}).superRefine((line, context) => {
  if (!line.product_id && !line.description.trim()) context.addIssue({ code: "custom", path: ["description"], message: "Add a product or free-description line" });
  if (!line.hsn_sac) context.addIssue({ code: "custom", path: ["hsn_sac"], message: "HSN/SAC is required" });
  if (line.product_id && !line.unit_price) context.addIssue({ code: "custom", path: ["unit_price"], message: "Unit price is required" });
});

export const invoiceDraftSchema = z.object({
  customer_id: z.string().min(1, "Select a customer"),
  place_of_supply_code: z.string().length(2, "Select place of supply"),
  doc_type: z.enum(["tax_invoice", "bill_of_supply", "proforma"]),
  reverse_charge: z.boolean(),
  invoice_date: z.string().min(1, "Invoice date is required"),
  due_date: z.string(),
  payment_terms: z.string(),
  notes: z.string(),
  lines: z.array(lineSchema).min(1, "Add at least one invoice line"),
});
export type InvoiceDraftFormValues = z.infer<typeof invoiceDraftSchema>;
type ValidationIssue = { line?: number; field?: string; message: string };

const blankLine: InvoiceDraftFormValues["lines"][number] = {
  product_id: "",
  serial_id: "",
  description: "",
  hsn_sac: "",
  unit: "",
  quantity: "1",
  unit_price: "",
  discount_mode: "percent",
  discount_pct: "0",
  discount_amount: "0",
  gst_rate: "",
  cess_rate: "0",
};

function blankDraft(): InvoiceDraftFormValues {
  return {
    customer_id: "",
    place_of_supply_code: "",
    doc_type: "tax_invoice",
    reverse_charge: false,
    invoice_date: new Date().toISOString().slice(0, 10),
    due_date: "",
    payment_terms: "",
    notes: "",
    lines: [{ ...blankLine }],
  };
}

export function invoiceDraftRequestBody(values: InvoiceDraftFormValues) {
  return {
    ...previewBody(values),
    invoice_date: values.invoice_date,
    due_date: values.due_date || null,
    payment_terms: values.payment_terms || null,
    notes: values.notes || null,
  };
}

function formFromDraft(draft: InvoiceDraft): InvoiceDraftFormValues {
  return {
    customer_id: String(draft.customer_id),
    place_of_supply_code: draft.place_of_supply_code,
    doc_type: draft.doc_type,
    reverse_charge: draft.reverse_charge,
    invoice_date: draft.invoice_date,
    due_date: draft.due_date ?? "",
    payment_terms: draft.payment_terms ?? "",
    notes: draft.notes ?? "",
    lines: draft.lines.map((line) => ({
      product_id: line.product_id === null ? "" : String(line.product_id),
      serial_id: line.serial_id === null ? "" : String(line.serial_id),
      description: line.description,
      hsn_sac: line.hsn_sac,
      unit: line.unit ?? "",
      quantity: line.quantity,
      unit_price: line.unit_price,
      discount_mode: "amount",
      discount_pct: "0",
      discount_amount: line.discount,
      gst_rate: line.gst_rate,
      cess_rate: line.cess_rate,
    })),
  };
}

function previewBody(values: InvoiceDraftFormValues) {
  return {
    customer_id: Number(values.customer_id),
    place_of_supply_code: values.place_of_supply_code,
    doc_type: values.doc_type,
    reverse_charge: values.reverse_charge,
    lines: values.lines.map((line) => ({
      product_id: line.product_id ? Number(line.product_id) : null,
      serial_id: line.serial_id ? Number(line.serial_id) : null,
      description: line.description || null,
      hsn_sac: line.hsn_sac || null,
      unit: line.unit || null,
      quantity: line.quantity,
      unit_price: line.unit_price || null,
      discount_pct: line.discount_mode === "percent" ? line.discount_pct : "0",
      discount_amount: line.discount_mode === "amount" ? line.discount_amount : "0",
      gst_rate: line.gst_rate || null,
      cess_rate: line.cess_rate || null,
    })),
  };
}

function issueDetails(error: unknown): ValidationIssue[] {
  if (!(error instanceof ApiError) || !Array.isArray(error.detail)) return [];
  return error.detail.flatMap((item) => {
    if (!item || typeof item !== "object" || !("loc" in item)) return [];
    const loc = (item as { loc: unknown }).loc;
    const message = "msg" in item ? String((item as { msg: unknown }).msg) : error.message;
    if (!Array.isArray(loc)) return [{ message }];
    const linePosition = loc.findIndex((part) => part === "lines");
    const field = loc.length ? String(loc[loc.length - 1]) : undefined;
    return [{
      line: linePosition >= 0 && typeof loc[linePosition + 1] === "number" ? Number(loc[linePosition + 1]) : undefined,
      field,
      message,
    }];
  });
}

export function PreviewTotals({ preview, loading, error }: { preview: Preview | null; loading: boolean; error: string | null }) {
  if (loading) return <div role="status" className="py-8 text-center text-sm text-slate-500">Calculating preview…</div>;
  if (error) return <p role="alert" className="py-6 text-sm text-red-700">{error}</p>;
  if (!preview) return <p className="py-8 text-center text-sm text-slate-500">Complete invoice details to preview server-computed GST and totals.</p>;
  return (
    <div className="space-y-3">
      <dl className="space-y-2 text-sm">
        <div className="flex justify-between"><dt>Taxable total</dt><dd>{formatINR(preview.taxable_total)}</dd></div>
        {preview.tax_applicable && (
          <>
            <div className="flex justify-between"><dt>CGST</dt><dd>{formatINR(preview.cgst_total)}</dd></div>
            <div className="flex justify-between"><dt>SGST</dt><dd>{formatINR(preview.sgst_total)}</dd></div>
            <div className="flex justify-between"><dt>IGST</dt><dd>{formatINR(preview.igst_total)}</dd></div>
            <div className="flex justify-between"><dt>Cess</dt><dd>{formatINR(preview.cess_total)}</dd></div>
          </>
        )}
        <div className="flex justify-between"><dt>Round off</dt><dd>{formatINR(preview.round_off)}</dd></div>
        <div className="flex justify-between border-t border-ivory-200 pt-2 text-base font-semibold"><dt>Grand total</dt><dd>{formatINR(preview.grand_total)}</dd></div>
      </dl>
      <p className="text-xs text-slate-500">{preview.amount_in_words}</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead><tr><th className="py-2">HSN/SAC</th><th>Taxable</th><th>CGST</th><th>SGST</th><th>IGST</th></tr></thead>
          <tbody>{preview.hsn_summary.map((row) => <tr key={`${row.hsn_sac}:${row.gst_rate}:${row.cess_rate}`}><td className="py-1">{row.hsn_sac}</td><td>{formatINR(row.taxable)}</td><td>{formatINR(row.cgst)}</td><td>{formatINR(row.sgst)}</td><td>{formatINR(row.igst)}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}

function InvoiceLineEditor({
  index,
  form,
  removeLine,
  lineErrors,
}: {
  index: number;
  form: ReturnType<typeof useForm<InvoiceDraftFormValues>>;
  removeLine: () => void;
  lineErrors: ValidationIssue[];
}) {
  const [productSearch, setProductSearch] = useState("");
  const [serialSearch, setSerialSearch] = useState("");
  const productId = useWatch({ control: form.control, name: `lines.${index}.product_id` });
  const products = useQuery({
    queryKey: ["invoice-products", productSearch],
    queryFn: ({ signal }) => api<{ items: Product[] }>("/products", { query: { page: 1, page_size: 100, q: productSearch }, signal }),
    retry: false,
  });
  const listedProduct = products.data?.items.find((product) => String(product.id) === productId);
  const selectedProductQuery = useQuery({
    queryKey: ["invoice-product", productId],
    queryFn: ({ signal }) => api<Product>(`/products/${productId}`, { signal }),
    enabled: !!productId && !listedProduct,
    retry: false,
  });
  const selectedProduct = listedProduct ?? selectedProductQuery.data;
  const serials = useQuery({
    queryKey: ["invoice-product-serials", productId, serialSearch],
    queryFn: ({ signal }) => api<Serial[]>(`/products/${productId}/serials`, { query: { q: serialSearch }, signal }),
    enabled: !!selectedProduct?.is_serialized,
    retry: false,
  });
  const lineError = (field: string) => lineErrors.find((issue) => issue.field === field)?.message;
  const line = form.watch(`lines.${index}`);

  function chooseProduct(value: string) {
    const product = products.data?.items.find((candidate) => String(candidate.id) === value);
    form.setValue(`lines.${index}.product_id`, value, { shouldValidate: true });
    form.setValue(`lines.${index}.serial_id`, "");
    if (!product) return;
    form.setValue(`lines.${index}.description`, product.name);
    form.setValue(`lines.${index}.hsn_sac`, product.hsn_sac);
    form.setValue(`lines.${index}.unit`, product.unit);
    form.setValue(`lines.${index}.unit_price`, product.selling_price);
    form.setValue(`lines.${index}.gst_rate`, product.gst_rate);
    form.setValue(`lines.${index}.cess_rate`, product.cess_rate);
  }

  return (
    <fieldset className="space-y-3 rounded-lg border border-ivory-300 p-4">
      <legend className="px-2 text-sm font-medium text-slate-800">Line {index + 1}</legend>
      <label className="block text-sm font-medium text-slate-700">Search product
        <input value={productSearch} onChange={(event) => setProductSearch(event.target.value)} placeholder="Search SKU or product…" className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
      </label>
      <label className="block text-sm font-medium text-slate-700">Product (or leave blank for free description)
        <select value={productId} onChange={(event) => chooseProduct(event.target.value)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
          <option value="">Free-description line</option>
        {selectedProduct && !listedProduct && <option value={selectedProduct.id}>{selectedProduct.sku} · {selectedProduct.name}</option>}
        {(products.data?.items ?? []).filter((product) => product.is_active).map((product) => <option key={product.id} value={product.id}>{product.sku} · {product.name}</option>)}
        </select>
      </label>
      {products.isError && <p role="alert" className="text-xs text-red-700">{(products.error as Error).message}</p>}
      {selectedProductQuery.isError && <p role="alert" className="text-xs text-red-700">{(selectedProductQuery.error as Error).message}</p>}
      {selectedProduct?.is_serialized && (
        <label className="block text-sm font-medium text-slate-700">Available serial
          <input value={serialSearch} onChange={(event) => setSerialSearch(event.target.value)} placeholder="Search chassis, VIN or motor…" className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
          <select {...form.register(`lines.${index}.serial_id`)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
            <option value="">Select available serial</option>
            {(serials.data ?? []).map((serial) => {
              const label = [serial.chassis_no, serial.vin, serial.motor_no, serial.battery_serial].filter(Boolean).join(" · ");
              return <option key={serial.id} value={serial.id}>{label}</option>;
            })}
          </select>
          {(form.formState.errors.lines?.[index]?.serial_id?.message ?? lineError("serial_id")) && <span role="alert" className="mt-1 block text-xs text-red-700">{form.formState.errors.lines?.[index]?.serial_id?.message ?? lineError("serial_id")}</span>}
          {serials.isError && <span role="alert" className="mt-1 block text-xs text-red-700">{(serials.error as Error).message}</span>}
        </label>
      )}
      {!productId && (
        <Field label="Description" {...form.register(`lines.${index}.description`)} error={form.formState.errors.lines?.[index]?.description?.message ?? lineError("description")} required />
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="HSN/SAC" {...form.register(`lines.${index}.hsn_sac`)} error={form.formState.errors.lines?.[index]?.hsn_sac?.message ?? lineError("hsn_sac")} required maxLength={8} />
        <Field label="Unit" {...form.register(`lines.${index}.unit`)} />
        <Field label="Quantity" type="number" min="0.001" step="0.001" {...form.register(`lines.${index}.quantity`)} error={form.formState.errors.lines?.[index]?.quantity?.message ?? lineError("quantity")} required />
        <Field label="Unit price" type="number" min="0" step="0.0001" {...form.register(`lines.${index}.unit_price`)} error={form.formState.errors.lines?.[index]?.unit_price?.message ?? lineError("unit_price")} required />
        <label className="block text-sm font-medium text-slate-700">Discount type
          <select {...form.register(`lines.${index}.discount_mode`)} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"><option value="percent">Percent</option><option value="amount">Amount</option></select>
        </label>
        {line.discount_mode === "percent" ? (
          <Field label="Discount (%)" type="number" min="0" max="100" step="0.01" {...form.register(`lines.${index}.discount_pct`)} error={form.formState.errors.lines?.[index]?.discount_pct?.message} />
        ) : (
          <Field label="Discount amount" type="number" min="0" step="0.01" {...form.register(`lines.${index}.discount_amount`)} error={form.formState.errors.lines?.[index]?.discount_amount?.message} />
        )}
        <Field label="GST rate (%)" type="number" min="0" step="0.01" {...form.register(`lines.${index}.gst_rate`)} error={form.formState.errors.lines?.[index]?.gst_rate?.message ?? lineError("gst_rate")} required />
        <Field label="Cess rate (%)" type="number" min="0" step="0.01" {...form.register(`lines.${index}.cess_rate`)} error={form.formState.errors.lines?.[index]?.cess_rate?.message} required />
      </div>
      {lineErrors.filter((issue) => !issue.field || issue.field === "lines").map((issue, itemIndex) => <p role="alert" key={itemIndex} className="text-sm text-red-700">{issue.message}</p>)}
      <div className="flex justify-end"><Button type="button" variant="ghost" className="text-red-700" onClick={removeLine}><Trash2 className="h-4 w-4" aria-hidden /> Remove line</Button></div>
    </fieldset>
  );
}

function InvoiceDraftEditor({
  draft,
  readiness,
  onCancel,
  onSaved,
}: {
  draft: InvoiceDraft | null;
  readiness: boolean;
  onCancel: () => void;
  onSaved: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [customerSearch, setCustomerSearch] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [validationIssues, setValidationIssues] = useState<ValidationIssue[]>([]);
  const initialValues = useMemo(() => draft ? formFromDraft(draft) : blankDraft(), [draft]);
  const form = useForm<InvoiceDraftFormValues>({ resolver: zodResolver(invoiceDraftSchema), defaultValues: initialValues });
  const fieldArray = useFieldArray({ control: form.control, name: "lines" });
  const watched = useWatch({ control: form.control });
  const selectedCustomerId = useWatch({ control: form.control, name: "customer_id" });
  const customers = useQuery({
    queryKey: ["invoice-customers", customerSearch],
    queryFn: ({ signal }) => api<{ items: Customer[] }>("/customers", { query: { page: 1, page_size: 100, q: customerSearch, is_active: true }, signal }),
    retry: false,
  });
  const listedCustomer = customers.data?.items.find((item) => String(item.id) === selectedCustomerId);
  const selectedCustomerQuery = useQuery({
    queryKey: ["invoice-customer", selectedCustomerId],
    queryFn: ({ signal }) => api<Customer>(`/customers/${selectedCustomerId}`, { signal }),
    enabled: !!selectedCustomerId && !listedCustomer,
    retry: false,
  });
  const selectedCustomer = listedCustomer ?? selectedCustomerQuery.data;
  const states = useQuery({ queryKey: ["states"], queryFn: ({ signal }) => api<State[]>("/states", { signal }), retry: false });

  useEffect(() => {
    if (draft) form.reset(formFromDraft(draft));
  }, [draft, form]);

  const parsed = invoiceDraftSchema.safeParse(watched);
  const request = parsed.success ? previewBody(parsed.data) : null;
  const requestKey = request ? JSON.stringify(request) : "";

  useEffect(() => {
    if (!readiness || !request) {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    const controller = new AbortController();
    setPreviewLoading(true);
    setPreviewError(null);
    const timer = setTimeout(() => {
      api<Preview>("/invoices/preview", { method: "POST", body: request, signal: controller.signal })
        .then((result) => setPreview(result))
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          setPreview(null);
          setPreviewError(error instanceof Error ? error.message : "Could not calculate invoice preview.");
          setValidationIssues(issueDetails(error));
        })
        .finally(() => { if (!controller.signal.aborted) setPreviewLoading(false); });
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [requestKey, readiness]);

  const save = useMutation({
    mutationFn: (values: InvoiceDraftFormValues) => api<InvoiceDraft>(
      draft ? `/invoices/${draft.id}` : "/invoices",
      { method: draft ? "PUT" : "POST", body: invoiceDraftRequestBody(values) },
    ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["invoices", "list"] });
      toast("success", "Invoice draft saved.");
      onSaved();
    },
    onError: (error) => {
      setValidationIssues(issueDetails(error));
      toast("error", error instanceof Error ? error.message : "Could not save invoice draft.");
    },
  });

  const errors = form.formState.errors;
  const headerError = (field: string) => validationIssues.find((issue) => issue.line === undefined && issue.field === field)?.message;
  const submit = form.handleSubmit((values) => {
    setValidationIssues([]);
    save.mutate(values);
  });

  return (
    <form onSubmit={submit} className="space-y-4">
      <fieldset disabled={!readiness} className="space-y-4">
      <div className="flex items-center justify-between">
        <div><h2 className="text-xl font-semibold text-forest-900">{draft ? "Edit invoice draft" : "New invoice draft"}</h2><p className="text-sm text-slate-500">Drafts do not receive invoice numbers and do not change stock.</p></div>
        <Button type="button" variant="secondary" onClick={onCancel}><ArrowLeft className="h-4 w-4" aria-hidden /> Back to invoices</Button>
      </div>
      <Card className="p-5">
        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm font-medium text-slate-700">Search customer
            <input value={customerSearch} onChange={(event) => setCustomerSearch(event.target.value)} placeholder="Search name or code…" className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
          </label>
          <label className="block text-sm font-medium text-slate-700">Customer
            <select {...form.register("customer_id")} onChange={(event) => {
              form.setValue("customer_id", event.target.value, { shouldValidate: true });
              const customer = customers.data?.items.find((item) => String(item.id) === event.target.value);
              if (customer?.state_code) form.setValue("place_of_supply_code", customer.state_code, { shouldValidate: true });
            }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
              <option value="">Select active customer</option>
              {selectedCustomer && !listedCustomer && <option value={selectedCustomer.id}>{selectedCustomer.code} · {selectedCustomer.name}</option>}
              {(customers.data?.items ?? []).filter((customer) => customer.is_active).map((customer) => <option key={customer.id} value={customer.id}>{customer.code} · {customer.name}</option>)}
            </select>
            {(errors.customer_id?.message ?? headerError("customer_id")) && <span role="alert" className="mt-1 block text-xs text-red-700">{errors.customer_id?.message ?? headerError("customer_id")}</span>}
            {selectedCustomerQuery.isError && <span role="alert" className="mt-1 block text-xs text-red-700">{(selectedCustomerQuery.error as Error).message}</span>}
          </label>
          <label className="block text-sm font-medium text-slate-700">Place of supply
            <select {...form.register("place_of_supply_code")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"><option value="">Select state</option>{(states.data ?? []).map((state) => <option key={state.code} value={state.code}>{state.code} · {state.name}</option>)}</select>
            {(errors.place_of_supply_code?.message ?? headerError("place_of_supply_code")) && <span role="alert" className="mt-1 block text-xs text-red-700">{errors.place_of_supply_code?.message ?? headerError("place_of_supply_code")}</span>}
          </label>
          <label className="block text-sm font-medium text-slate-700">Document type
            <select {...form.register("doc_type")} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm"><option value="tax_invoice">Tax invoice</option><option value="bill_of_supply">Bill of supply</option><option value="proforma">Proforma</option></select>
          </label>
          <Field label="Invoice date" type="date" {...form.register("invoice_date")} error={errors.invoice_date?.message ?? headerError("invoice_date")} required />
          <Field label="Due date" type="date" {...form.register("due_date")} error={errors.due_date?.message ?? headerError("due_date")} />
          <Field label="Payment terms" {...form.register("payment_terms")} error={errors.payment_terms?.message ?? headerError("payment_terms")} maxLength={1000} />
          <label className="flex items-center gap-2 text-sm text-slate-700"><input type="checkbox" {...form.register("reverse_charge")} />Reverse charge</label>
          <label className="text-sm font-medium text-slate-700 sm:col-span-2">Notes
            <textarea {...form.register("notes")} rows={2} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm" />
            {headerError("notes") && <span role="alert" className="mt-1 block text-xs text-red-700">{headerError("notes")}</span>}
          </label>
        </div>
      </Card>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <div className="space-y-3">
          {fieldArray.fields.map((field, index) => (
            <InvoiceLineEditor
              key={field.id}
              index={index}
              form={form}
              lineErrors={validationIssues.filter((issue) => issue.line === index)}
              removeLine={() => fieldArray.remove(index)}
            />
          ))}
          {errors.lines?.root?.message && <p role="alert" className="text-sm text-red-700">{errors.lines.root.message}</p>}
          <Button type="button" variant="secondary" onClick={() => fieldArray.append({ ...blankLine })}><Plus className="h-4 w-4" aria-hidden /> Add line</Button>
        </div>
        <Card className="h-fit p-5">
          <h3 className="mb-3 text-lg font-semibold text-forest-900">Server-calculated totals</h3>
          <PreviewTotals preview={preview} loading={previewLoading} error={previewError} />
        </Card>
      </div>
      </fieldset>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="secondary" onClick={onCancel} disabled={save.isPending}>Cancel</Button>
        <Button type="submit" loading={save.isPending} disabled={!readiness || previewLoading || !preview}>
          Save draft
        </Button>
      </div>
    </form>
  );
}

export function InvoicesPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [invoiceStatus, setInvoiceStatus] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [newDraft, setNewDraft] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<InvoiceListRow | null>(null);
  const [ready, setReady] = useState(false);
  const canCreate = can("sales:create");
  const canUpdate = can("sales:update");

  const readiness = useQuery({
    queryKey: ["settings", "readiness"],
    queryFn: ({ signal }) => api<{ ready: boolean; missing_fields: string[] }>("/settings/readiness", { signal }),
    retry: false,
  });
  useEffect(() => setReady(!!readiness.data?.ready && !readiness.isError), [readiness.data, readiness.isError]);

  const list = useQuery({
    queryKey: ["invoices", "list", invoiceStatus],
    queryFn: ({ signal }) => api<InvoicePageData>("/invoices", { query: { page: 1, page_size: 100, status: invoiceStatus }, signal }),
    retry: false,
  });
  const detail = useQuery({
    queryKey: ["invoices", "detail", editId],
    queryFn: ({ signal }) => api<InvoiceDraft>(`/invoices/${editId}`, { signal }),
    enabled: editId !== null,
    retry: false,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api<void>(`/invoices/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["invoices", "list"] });
      setDeleteTarget(null);
      toast("success", "Invoice draft deleted.");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Could not delete invoice draft."),
  });

  if (newDraft || editId !== null) {
    if (editId !== null && detail.isLoading) return <LoadingState />;
    if (editId !== null && detail.isError) return <ErrorState message={(detail.error as Error).message} onRetry={() => detail.refetch()} />;
    const draft = editId === null ? null : detail.data ?? null;
    return (
      <div className="mx-auto max-w-7xl space-y-4">
        <InvoiceReadinessBanner />
        <InvoiceDraftEditor
          key={editId ?? "new"}
          draft={draft}
          readiness={ready}
          onCancel={() => { setEditId(null); setNewDraft(false); }}
          onSaved={() => { setEditId(null); setNewDraft(false); }}
        />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold text-forest-900">Invoices</h1><p className="text-sm text-slate-500">Prepare and save GST invoice drafts. Issuing and numbering are not available in this phase.</p></div>
        {canCreate && <Button onClick={() => setNewDraft(true)} disabled={!ready}><Plus className="h-4 w-4" aria-hidden /> New draft</Button>}
      </div>
      <InvoiceReadinessBanner />
      <div className="flex justify-end">
        <select aria-label="Filter invoices by status" value={invoiceStatus} onChange={(event) => setInvoiceStatus(event.target.value)} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
          <option value="">Draft and issued</option><option value="draft">Draft</option><option value="issued">Issued</option>
        </select>
      </div>
      <Card className="overflow-hidden">
        {list.isLoading && <LoadingState />}
        {list.isError && <ErrorState message={(list.error as Error).message} onRetry={() => list.refetch()} />}
        {list.isSuccess && list.data.items.length === 0 && <EmptyState title="No invoices found" hint="Create a draft invoice when company readiness is complete." />}
        {list.isSuccess && list.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500"><tr>{["Invoice", "Customer", "Type", "Date", "Status", "Total"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>)}<th className="px-4 py-3 text-right">Actions</th></tr></thead>
              <tbody className="divide-y divide-ivory-200">
                {list.data.items.map((invoice) => (
                  <tr key={invoice.id} className="hover:bg-ivory-50">
                    <td className="px-4 py-3">{invoice.invoice_number ?? "Draft (not numbered)"}</td>
                    <td className="px-4 py-3 font-medium">{invoice.customer_name}</td>
                    <td className="px-4 py-3">{invoice.doc_type.replace(/_/g, " ")}</td>
                    <td className="px-4 py-3">{formatDate(invoice.invoice_date)}</td>
                    <td className="px-4 py-3 capitalize">{invoice.status}</td>
                    <td className="px-4 py-3 tabular-nums">{formatINR(invoice.grand_total)}</td>
                    <td className="px-4 py-3">
                      {invoice.status === "draft" && canUpdate && (
                        <div className="flex justify-end gap-1">
                          <Button variant="secondary" disabled={!ready} onClick={() => setEditId(invoice.id)}>Edit draft</Button>
                          <Button variant="ghost" className="px-2 text-red-700" disabled={!ready} onClick={() => setDeleteTarget(invoice)} aria-label={`Delete draft for ${invoice.customer_name}`}><Trash2 className="h-4 w-4" aria-hidden /></Button>
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete invoice draft"
        message={`Delete the draft for ${deleteTarget?.customer_name ?? "this customer"}? This cannot be undone.`}
        confirmLabel="Delete draft"
        danger
        onCancel={() => { if (!remove.isPending) setDeleteTarget(null); }}
        onConfirm={async () => { if (deleteTarget) await remove.mutateAsync(deleteTarget.id); }}
      />
    </div>
  );
}
