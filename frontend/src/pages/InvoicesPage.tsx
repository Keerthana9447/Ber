import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useFieldArray, useForm, useWatch } from "react-hook-form";
import { z } from "zod";
import { ArrowLeft, Plus, Trash2 } from "lucide-react";
import { Badge, Button, Card, ConfirmDialog, EmptyState, ErrorState, Field, LoadingState, Modal, useToast } from "../components/ui";
import { InvoiceReadinessBanner } from "../components/InvoiceReadinessBanner";
import { api, apiBlob, ApiError } from "../lib/api";
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
type Serial = { id: number; chassis_no: string | null; vin: string | null; motor_no: string | null; battery_serial: string | null; warehouse_id: number | null };
type Warehouse = { id: number; code: string; name: string };
type InvoiceListRow = {
  id: number;
  invoice_number: string | null;
  customer_id: number;
  customer_name: string;
  doc_type: string;
  invoice_date: string;
  due_date: string | null;
  status: "draft" | "approved" | "issued" | "cancelled";
  grand_total: string;
};
type InvoicePageData = { items: InvoiceListRow[]; total: number };
type InvoiceLineResponse = {
  id?: number;
  product_id: number | null;
  serial_id: number | null;
  warehouse_id: number | null;
  is_service: boolean;
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
  invoice_number: string | null;
  customer_id: number;
  place_of_supply_code: string;
  doc_type: "tax_invoice" | "bill_of_supply" | "proforma";
  reverse_charge: boolean;
  invoice_date: string;
  due_date: string | null;
  payment_terms: string | null;
  notes: string | null;
  status: string;
  taxable_total: string;
  cgst_total: string;
  sgst_total: string;
  igst_total: string;
  cess_total: string;
  round_off: string;
  grand_total: string;
  lines: InvoiceLineResponse[];
};
type PreviewLine = {
  product_id: number | null;
  serial_id: number | null;
  warehouse_id: number | null;
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
  warehouse_id: z.string(),
  is_service: z.boolean(),
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
  if (line.product_id && !line.is_service && !line.warehouse_id) context.addIssue({ code: "custom", path: ["warehouse_id"], message: "Select a warehouse for this stocked product" });
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
  warehouse_id: "",
  is_service: false,
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
      warehouse_id: line.warehouse_id === null ? "" : String(line.warehouse_id),
      is_service: line.is_service,
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
      warehouse_id: line.warehouse_id && !line.is_service ? Number(line.warehouse_id) : null,
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
  const productOptions = useQuery({
    queryKey: ["invoice-product-options"],
    queryFn: ({ signal }) => api<{ warehouses: Warehouse[] }>("/product-options", { signal }),
    retry: false,
  });
  const serials = useQuery({
    queryKey: ["invoice-product-serials", productId, serialSearch],
    queryFn: ({ signal }) => api<Serial[]>(`/products/${productId}/serials`, { query: { q: serialSearch }, signal }),
    enabled: !!selectedProduct?.is_serialized,
    retry: false,
  });
  const lineError = (field: string) => lineErrors.find((issue) => issue.field === field)?.message;
  const line = form.watch(`lines.${index}`);

  useEffect(() => {
    if (selectedProduct && !selectedProduct.is_service && !selectedProduct.is_serialized
      && !form.getValues(`lines.${index}.warehouse_id`) && productOptions.data?.warehouses[0]) {
      form.setValue(`lines.${index}.warehouse_id`, String(productOptions.data.warehouses[0].id), { shouldValidate: true });
    }
  }, [selectedProduct, productOptions.data, form, index]);

  function chooseProduct(value: string) {
    const product = products.data?.items.find((candidate) => String(candidate.id) === value);
    form.setValue(`lines.${index}.product_id`, value, { shouldValidate: true });
    form.setValue(`lines.${index}.serial_id`, "");
    form.setValue(`lines.${index}.warehouse_id`, "");
    form.setValue(`lines.${index}.is_service`, product?.is_service ?? false);
    if (!product) return;
    form.setValue(`lines.${index}.description`, product.name);
    form.setValue(`lines.${index}.hsn_sac`, product.hsn_sac);
    form.setValue(`lines.${index}.unit`, product.unit);
    form.setValue(`lines.${index}.unit_price`, product.selling_price);
    form.setValue(`lines.${index}.gst_rate`, product.gst_rate);
    form.setValue(`lines.${index}.cess_rate`, product.cess_rate);
    if (!product.is_service && !product.is_serialized && productOptions.data?.warehouses[0]) {
      form.setValue(`lines.${index}.warehouse_id`, String(productOptions.data.warehouses[0].id), { shouldValidate: true });
    }
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
          <select {...form.register(`lines.${index}.serial_id`)} onChange={(event) => {
            form.setValue(`lines.${index}.serial_id`, event.target.value, { shouldValidate: true });
            const serial = serials.data?.find((item) => String(item.id) === event.target.value);
            form.setValue(`lines.${index}.warehouse_id`, serial?.warehouse_id ? String(serial.warehouse_id) : "", { shouldValidate: true });
          }} className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
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
      {selectedProduct && !selectedProduct.is_service && (
        <label className="block text-sm font-medium text-slate-700">Warehouse
          <select
            value={line.warehouse_id}
            disabled={selectedProduct.is_serialized}
            onChange={(event) => form.setValue(`lines.${index}.warehouse_id`, event.target.value, { shouldValidate: true })}
            className="mt-1 w-full rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm disabled:bg-ivory-100"
          >
            <option value="">Select warehouse</option>
            {(productOptions.data?.warehouses ?? []).map((warehouse) => <option key={warehouse.id} value={warehouse.id}>{warehouse.name}</option>)}
          </select>
          {(form.formState.errors.lines?.[index]?.warehouse_id?.message ?? lineError("warehouse_id")) && <span role="alert" className="mt-1 block text-xs text-red-700">{form.formState.errors.lines?.[index]?.warehouse_id?.message ?? lineError("warehouse_id")}</span>}
          {productOptions.isError && <span role="alert" className="mt-1 block text-xs text-red-700">{(productOptions.error as Error).message}</span>}
        </label>
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

type WorkflowAction = "approve" | "issue" | "convert";
type WorkflowTarget = { id: number; customer_name: string; action: WorkflowAction };

export function InvoiceReadOnlyView({
  invoice,
  onBack,
  onDownload,
  downloading,
  onCancel,
}: {
  invoice: InvoiceDraft;
  onBack: () => void;
  onDownload: () => void;
  downloading: boolean;
  onCancel: () => void;
}) {
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold text-forest-900">{invoice.invoice_number ?? "Invoice draft"}</h1>
          <div className="mt-1 flex items-center gap-2 text-sm text-slate-600"><span>{formatDate(invoice.invoice_date)}</span><Badge>{invoice.status}</Badge></div>
        </div>
        <div className="flex gap-2">
          {(invoice.status === "issued" || invoice.status === "cancelled") && <Button loading={downloading} onClick={onDownload}>Download PDF</Button>}
          {invoice.status === "issued" && <Button variant="danger" onClick={onCancel}>Cancel invoice</Button>}
          <Button variant="secondary" onClick={onBack}>Back to invoices</Button>
        </div>
      </div>
      <Card className="overflow-x-auto p-5">
        <p className="mb-3 text-sm text-slate-600">Customer ID {invoice.customer_id} · Place of supply {invoice.place_of_supply_code}</p>
        <table className="w-full min-w-[700px] text-left text-sm">
          <thead><tr className="border-b border-ivory-300"><th className="py-2">Description / HSN</th><th>Qty</th><th>Price</th><th>Taxable</th><th>GST</th></tr></thead>
          <tbody>{invoice.lines.map((line, index) => (
            <tr key={line.id ?? index} className="border-b border-ivory-200">
              <td className="py-2">{line.description} <span className="text-slate-500">({line.hsn_sac})</span></td>
              <td>{line.quantity} {line.unit}</td><td>{formatINR(line.unit_price)}</td><td>{formatINR(line.taxable_value)}</td><td>{line.gst_rate}%</td>
            </tr>
          ))}</tbody>
        </table>
        <dl className="ml-auto mt-5 max-w-sm space-y-1 text-sm">
          <div className="flex justify-between"><dt>Taxable</dt><dd>{formatINR(invoice.taxable_total)}</dd></div>
          <div className="flex justify-between"><dt>CGST / SGST</dt><dd>{formatINR(invoice.cgst_total)} / {formatINR(invoice.sgst_total)}</dd></div>
          <div className="flex justify-between"><dt>IGST / Cess</dt><dd>{formatINR(invoice.igst_total)} / {formatINR(invoice.cess_total)}</dd></div>
          <div className="flex justify-between"><dt>Round off</dt><dd>{formatINR(invoice.round_off)}</dd></div>
          <div className="flex justify-between border-t border-ivory-300 pt-2 font-semibold"><dt>Grand total</dt><dd>{formatINR(invoice.grand_total)}</dd></div>
        </dl>
      </Card>
    </div>
  );
}

export function InvoicesPage() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const { can } = useAuth();
  const [invoiceStatus, setInvoiceStatus] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [viewId, setViewId] = useState<number | null>(null);
  const [newDraft, setNewDraft] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<InvoiceListRow | null>(null);
  const [workflowTarget, setWorkflowTarget] = useState<WorkflowTarget | null>(null);
  const [cancelTarget, setCancelTarget] = useState<number | null>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [downloadingId, setDownloadingId] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  const canCreate = can("sales:create");
  const canUpdate = can("sales:update");
  const canApprove = can("sales:approve");
  const canIssue = can("sales:issue");
  const canCancel = can("sales:cancel");
  const detailId = editId ?? viewId;

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
    queryKey: ["invoices", "detail", detailId],
    queryFn: ({ signal }) => api<InvoiceDraft>(`/invoices/${detailId}`, { signal }),
    enabled: detailId !== null,
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
  const workflow = useMutation({
    mutationFn: ({ id, action, reason }: { id: number; action: WorkflowAction | "cancel"; reason?: string }) => {
      if (action === "cancel") return api<InvoiceDraft>(`/invoices/${id}/cancel`, { method: "POST", body: { reason } });
      return api<InvoiceDraft>(`/invoices/${id}/${action === "convert" ? "convert-to-tax-invoice" : action}`, { method: "POST" });
    },
    onSuccess: async (_result, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["invoices", "list"] }),
        queryClient.invalidateQueries({ queryKey: ["invoices", "detail", variables.id] }),
        queryClient.invalidateQueries({ queryKey: ["inventory"] }),
      ]);
      const messages = { approve: "Invoice approved.", issue: "Invoice issued.", cancel: "Invoice cancelled and stock restored.", convert: "New tax invoice draft created from proforma." };
      toast("success", messages[variables.action]);
      setWorkflowTarget(null);
      setCancelTarget(null);
      setCancelReason("");
    },
    onError: (error) => toast("error", error instanceof Error ? error.message : "Invoice action failed."),
  });

  async function downloadPdf(id: number) {
    setDownloadingId(id);
    try {
      const blob = await apiBlob(`/invoices/${id}/pdf`);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${detail.data?.invoice_number ?? `invoice-${id}`}.pdf`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      toast("success", "Invoice PDF downloaded.");
    } catch (error) {
      toast("error", error instanceof Error ? error.message : "Could not download invoice PDF.");
    } finally {
      setDownloadingId(null);
    }
  }

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
  if (viewId !== null) {
    if (detail.isLoading) return <LoadingState />;
    if (detail.isError || !detail.data) return <ErrorState message={(detail.error as Error | undefined)?.message ?? "Invoice not found"} onRetry={() => detail.refetch()} />;
    return (
      <div className="mx-auto max-w-7xl space-y-4">
        <InvoiceReadinessBanner />
        <InvoiceReadOnlyView invoice={detail.data} onBack={() => setViewId(null)} onDownload={() => downloadPdf(viewId)} downloading={downloadingId === viewId} onCancel={() => setCancelTarget(viewId)} />
        {can("sales:create") && detail.data.doc_type === "proforma" && detail.data.status === "draft" && (
          <Button onClick={() => setWorkflowTarget({ id: viewId, customer_name: "proforma", action: "convert" })}>Convert to tax invoice draft</Button>
        )}
      </div>
    );
  }

  const workflowMessages: Record<WorkflowAction, { title: string; message: string; label: string }> = {
    approve: { title: "Approve invoice", message: "Approve this invoice draft? It remains editable only while it is a draft.", label: "Approve" },
    issue: { title: "Issue invoice", message: "This assigns an invoice number, reduces stock and posts accounting entries and cannot be edited afterwards.", label: "Issue invoice" },
    convert: { title: "Convert proforma", message: "Create a new tax-invoice draft with the same customer and lines. The proforma stays unchanged; no number, stock or accounting entry is created.", label: "Create draft" },
  };
  const activeWorkflow = workflowTarget ? workflowMessages[workflowTarget.action] : null;

  return (
    <div className="mx-auto max-w-7xl space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold text-forest-900">Invoices</h1><p className="text-sm text-slate-500">Create drafts, approve and issue GST invoices, and download stored invoice PDFs.</p></div>
        {canCreate && <Button onClick={() => setNewDraft(true)} disabled={!ready}><Plus className="h-4 w-4" aria-hidden /> New draft</Button>}
      </div>
      <InvoiceReadinessBanner />
      <div className="flex justify-end">
        <select aria-label="Filter invoices by status" value={invoiceStatus} onChange={(event) => setInvoiceStatus(event.target.value)} className="rounded-lg border border-ivory-300 bg-white px-3 py-2 text-sm">
          <option value="">All statuses</option><option value="draft">Draft</option><option value="approved">Approved</option><option value="issued">Issued</option><option value="cancelled">Cancelled</option>
        </select>
      </div>
      {workflow.isError && <p role="alert" className="text-sm text-red-700">{(workflow.error as Error).message}</p>}
      <Card className="overflow-hidden">
        {list.isLoading && <LoadingState />}
        {list.isError && <ErrorState message={(list.error as Error).message} onRetry={() => list.refetch()} />}
        {list.isSuccess && list.data.items.length === 0 && <EmptyState title="No invoices found" hint="Create a draft invoice when company readiness is complete." />}
        {list.isSuccess && list.data.items.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[1000px] text-left text-sm">
              <thead className="bg-ivory-100 text-xs uppercase tracking-wide text-slate-500"><tr>{["Invoice", "Customer", "Type", "Date", "Status", "Total"].map((label) => <th key={label} scope="col" className="px-4 py-3 font-medium">{label}</th>)}<th className="px-4 py-3 text-right">Actions</th></tr></thead>
              <tbody className="divide-y divide-ivory-200">
                {list.data.items.map((invoice) => (
                  <tr key={invoice.id} className="hover:bg-ivory-50">
                    <td className="px-4 py-3"><button className="text-forest-800 underline" onClick={() => setViewId(invoice.id)}>{invoice.invoice_number ?? "Draft (not numbered)"}</button></td>
                    <td className="px-4 py-3 font-medium">{invoice.customer_name}</td>
                    <td className="px-4 py-3">{invoice.doc_type.replace(/_/g, " ")}</td>
                    <td className="px-4 py-3">{formatDate(invoice.invoice_date)}</td>
                    <td className="px-4 py-3"><Badge>{invoice.status}</Badge></td>
                    <td className="px-4 py-3 tabular-nums">{formatINR(invoice.grand_total)}</td>
                    <td className="px-4 py-3">
                      <div className="flex justify-end gap-1">
                        {invoice.status === "draft" && canUpdate && <Button variant="secondary" disabled={!ready || workflow.isPending} onClick={() => setEditId(invoice.id)}>Edit</Button>}
                        {invoice.status === "draft" && canApprove && invoice.doc_type !== "proforma" && <Button variant="secondary" disabled={!ready || workflow.isPending} onClick={() => setWorkflowTarget({ id: invoice.id, customer_name: invoice.customer_name, action: "approve" })}>Approve</Button>}
                        {invoice.status === "approved" && canIssue && <Button disabled={!ready || workflow.isPending} onClick={() => setWorkflowTarget({ id: invoice.id, customer_name: invoice.customer_name, action: "issue" })}>Issue</Button>}
                        {invoice.status === "draft" && canCreate && invoice.doc_type === "proforma" && <Button variant="secondary" disabled={workflow.isPending} onClick={() => setWorkflowTarget({ id: invoice.id, customer_name: invoice.customer_name, action: "convert" })}>Convert</Button>}
                        {(invoice.status === "issued" || invoice.status === "cancelled") && <Button variant="secondary" disabled={downloadingId !== null} onClick={() => downloadPdf(invoice.id)}>PDF</Button>}
                        {invoice.status === "issued" && canCancel && <Button variant="danger" disabled={workflow.isPending} onClick={() => setCancelTarget(invoice.id)}>Cancel</Button>}
                        {invoice.status === "draft" && canUpdate && <Button variant="ghost" className="px-2 text-red-700" disabled={workflow.isPending} onClick={() => setDeleteTarget(invoice)} aria-label={`Delete draft for ${invoice.customer_name}`}><Trash2 className="h-4 w-4" aria-hidden /></Button>}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      <ConfirmDialog
        open={workflowTarget !== null}
        title={activeWorkflow?.title ?? ""}
        message={activeWorkflow?.message ?? ""}
        confirmLabel={activeWorkflow?.label}
        danger={workflowTarget?.action === "issue"}
        onCancel={() => { if (!workflow.isPending) setWorkflowTarget(null); }}
        onConfirm={async () => {
          if (workflowTarget) {
            try { await workflow.mutateAsync({ id: workflowTarget.id, action: workflowTarget.action }); } catch { /* Error is shown by the mutation toast. */ }
          }
        }}
      />
      <Modal open={cancelTarget !== null} onClose={() => { if (!workflow.isPending) setCancelTarget(null); }} title="Cancel invoice">
        <form className="space-y-4 p-6" onSubmit={(event) => {
          event.preventDefault();
          if (cancelTarget !== null) workflow.mutate({ id: cancelTarget, action: "cancel", reason: cancelReason.trim() });
        }}>
          <h2 className="text-lg font-semibold">Cancel issued invoice</h2>
          <p className="text-sm text-slate-600">This preserves the invoice number, reverses accounting entries, restores stock and serials, and cannot be undone.</p>
          <label className="block text-sm font-medium">Cancellation reason
            <textarea required minLength={10} value={cancelReason} onChange={(event) => setCancelReason(event.target.value)} rows={4} className="mt-1 w-full rounded-lg border border-ivory-300 p-3" />
          </label>
          <div className="flex justify-end gap-2">
            <Button type="button" variant="secondary" disabled={workflow.isPending} onClick={() => setCancelTarget(null)}>Keep invoice</Button>
            <Button type="submit" variant="danger" loading={workflow.isPending} disabled={cancelReason.trim().length < 10}>Cancel invoice</Button>
          </div>
        </form>
      </Modal>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="Delete invoice draft"
        message={`Delete the draft for ${deleteTarget?.customer_name ?? "this customer"}? This cannot be undone.`}
        confirmLabel="Delete draft"
        danger
        onCancel={() => { if (!remove.isPending) setDeleteTarget(null); }}
        onConfirm={async () => {
          if (deleteTarget) {
            try { await remove.mutateAsync(deleteTarget.id); } catch { /* Error is shown by the mutation toast. */ }
          }
        }}
      />
    </div>
  );
}
