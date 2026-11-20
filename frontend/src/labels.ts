// Display labels. UI chrome is English; AI content follows the user's language.

export const CATEGORY_LABELS: Record<string, string> = {
  damaged_or_defective: "Damaged / defective product",
  refund_not_received: "Refund not received",
  not_delivered: "Product not delivered",
  wrong_item: "Wrong product delivered",
  refund_or_replacement_refused: "Refund / replacement refused",
  other_ecommerce: "Other online-shopping dispute",
  out_of_scope: "Outside NyayaAI scope",
  unclear: "Not yet clear",
};

export const STATUS_LABELS: Record<string, string> = {
  intake: "Gathering facts",
  ready: "Ready for guidance",
  out_of_scope: "Out of scope",
};

export const LANG_LABELS: Record<string, string> = { en: "English", hi: "हिंदी", hinglish: "Hinglish" };

export const FIELD_LABELS: Record<string, string> = {
  title: "Case title",
  category: "Dispute category",
  seller_name: "Seller / company",
  platform: "Platform (website / app)",
  product_details: "Product details",
  order_id: "Order ID / invoice no.",
  purchase_date: "Purchase date",
  delivery_date: "Delivery date",
  amount_paid: "Amount paid",
  description: "What went wrong",
  previous_communication: "Communication with seller so far",
  evidence: "Evidence available",
  desired_resolution: "Resolution you want",
};

export const DOC_TYPES: { id: string; label: string; hint: string }[] = [
  { id: "seller_complaint", label: "Formal complaint to seller", hint: "Addressed to the seller / platform grievance officer" },
  { id: "refund_request", label: "Refund request", hint: "Short, firm request for your money back" },
  { id: "consumer_grievance", label: "Consumer grievance draft", hint: "Basis for NCH grievance or Consumer Commission complaint" },
  { id: "statement_of_facts", label: "Chronological statement of facts", hint: "Built directly from your case timeline (no AI)" },
];

export const DISCLAIMER =
  "NyayaAI provides legal information, not legal advice. It can be wrong. Verify with the cited official sources and consult a qualified lawyer for advice on your situation.";
