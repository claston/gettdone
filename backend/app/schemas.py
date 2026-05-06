from pydantic import BaseModel


class ReconciliationSummary(BaseModel):
    matched_groups: int
    reversed_entries: int
    potential_duplicates: int


class OperationalSummary(BaseModel):
    total_volume: float
    inflow_count: int
    outflow_count: int
    reconciled_entries: int
    unmatched_entries: int


class CategorySummary(BaseModel):
    category: str
    total: float
    count: int


class TopExpense(BaseModel):
    description: str
    amount: float
    date: str
    category: str


class Insight(BaseModel):
    type: str
    title: str
    description: str


class ReconcileSummary(BaseModel):
    total_bank_rows: int
    total_sheet_rows: int
    conciliated_count: int
    pending_count: int
    divergent_count: int


class TransactionPreview(BaseModel):
    date: str
    description: str
    amount: float
    category: str
    reconciliation_status: str
    is_deleted: bool = False


class BeforeAfterPreview(BaseModel):
    date: str
    description_before: str
    description_after: str
    amount_before: float
    amount_after: float


class PdfProcessingMetrics(BaseModel):
    total_ms: float
    parse_ms: float
    classify_ms: float
    normalize_ms: float
    reconcile_ms: float
    page_count: int
    extracted_char_count: int
    flattened_line_count: int
    grouped_transactions_count: int
    inline_candidates_count: int
    inline_transactions_count: int
    selected_parser: str


class AnalyzeResponse(BaseModel):
    analysis_id: str
    file_type: str
    semantic_type: str | None = None
    semantic_confidence: float | None = None
    semantic_evidence: list[str] | None = None
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    operational_summary: OperationalSummary
    reconciliation: ReconciliationSummary
    categories: list[CategorySummary]
    top_expenses: list[TopExpense]
    insights: list[Insight]
    preview_transactions: list[TransactionPreview]
    preview_before_after: list[BeforeAfterPreview]
    expires_at: str | None
    updated_at: str | None = None
    layout_inference_name: str | None = None
    layout_inference_confidence: float | None = None
    pdf_processing_metrics: PdfProcessingMetrics | None = None


class ReconcileIntakeResponse(BaseModel):
    analysis_id: str
    status: str
    bank_filename: str
    bank_file_type: str
    bank_semantic_type: str | None = None
    bank_semantic_confidence: float | None = None
    bank_semantic_evidence: list[str] | None = None
    sheet_filename: str
    sheet_file_type: str
    sheet_semantic_type: str | None = None
    sheet_semantic_confidence: float | None = None
    sheet_semantic_evidence: list[str] | None = None
    bank_rows_parsed: int
    sheet_rows_parsed: int
    sheet_mapping_detected: dict[str, str]
    normalization_preview: list[dict[str, str | float]]
    exact_matches_count: int
    date_tolerance_matches_count: int
    description_similarity_matches_count: int
    total_matches_count: int
    conciliated_count: int
    pending_count: int
    divergent_count: int
    bank_unmatched_count: int
    sheet_unmatched_count: int
    exact_matches_preview: list[dict[str, str | int | float]]
    date_tolerance_matches_preview: list[dict[str, str | int | float]]
    description_similarity_matches_preview: list[dict[str, str | int | float]]
    reconciliation_rows: list[dict[str, str | float | None]]
    problems: list[Insight]
    summary: ReconcileSummary
    expires_at: str | None


class ConvertResponse(BaseModel):
    processing_id: str
    quota_remaining: int
    quota_limit: int
    identity_type: str
    analysis: AnalyzeResponse


class ConvertEditPatch(BaseModel):
    row_id: str | None = None
    action: str = "update"
    insert_position: int | None = None
    date: str | None = None
    description: str | None = None
    credit: float | None = None
    debit: float | None = None


class ConvertEditsRequest(BaseModel):
    edits: list[ConvertEditPatch]
    expected_updated_at: str | None = None


class ConvertEditsResponse(BaseModel):
    processing_id: str
    transactions_total: int
    total_inflows: float
    total_outflows: float
    net_total: float
    preview_transactions: list[TransactionPreview]
    updated_at: str


class RegisterRequest(BaseModel):
    name: str
    email: str
    password: str


class RegisterResponse(BaseModel):
    user_id: str
    name: str
    email: str
    user_token: str
    quota_remaining: int
    quota_limit: int
    quota_mode: str = "conversion"
    plan_code: str | None = None
    plan_name: str | None = None
    max_upload_size_bytes: int
    max_pages_per_file: int


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    user_id: str
    name: str
    email: str
    user_token: str
    quota_remaining: int
    quota_limit: int
    quota_mode: str = "conversion"
    plan_code: str | None = None
    plan_name: str | None = None
    max_upload_size_bytes: int
    max_pages_per_file: int


class AuthMeResponse(BaseModel):
    user_id: str
    name: str
    email: str
    quota_remaining: int
    quota_limit: int
    quota_mode: str = "conversion"
    plan_code: str | None = None
    plan_name: str | None = None
    max_upload_size_bytes: int
    max_pages_per_file: int


class AdminLoginRequest(BaseModel):
    email: str
    password: str


class AdminLoginResponse(BaseModel):
    user_id: str
    name: str
    email: str
    admin_token: str
    role: str = "admin"


class AdminMeResponse(BaseModel):
    user_id: str
    name: str
    email: str
    role: str = "admin"


class PlanCatalogItem(BaseModel):
    id: str
    code: str
    name: str
    version: int
    currency: str
    price_cents: int
    billing_period: str
    quota_mode: str
    quota_limit: int
    quota_window_days: int
    max_upload_size_bytes: int
    max_pages_per_file: int


class PlanCatalogResponse(BaseModel):
    items: list[PlanCatalogItem]


class AdminActivatePlanRequest(BaseModel):
    user_id: str
    plan_code: str


class AdminActivatePlanResponse(BaseModel):
    user_id: str
    plan_code: str
    plan_name: str
    plan_version: int
    quota_mode: str
    quota_limit: int


class CheckoutIntentRequest(BaseModel):
    user_token: str
    plan_code: str
    name: str
    email: str
    whatsapp: str
    document: str | None = None
    notes: str | None = None
    accepted_terms: bool = False


class CheckoutIntentResponse(BaseModel):
    intent_id: str
    status: str
    created_at: str
    updated_at: str
    plan_code: str
    plan_name: str
    price_cents: int
    currency: str
    billing_period: str
    payment_link: str | None = None
    payment_link_sent_at: str | None = None
    released_at: str | None = None
    next_step: str
    admin_delivery_mode: str
    customer_delivery_mode: str
    message: str


class CheckoutIntentStatusResponse(BaseModel):
    intent_id: str
    status: str
    created_at: str
    updated_at: str
    plan_code: str
    plan_name: str
    price_cents: int
    currency: str
    billing_period: str
    payment_link: str | None = None
    payment_link_sent_at: str | None = None
    released_at: str | None = None
    next_step: str


class CheckoutIntentPaymentLinkRequest(BaseModel):
    payment_link: str


class AdminCheckoutIntentItem(BaseModel):
    intent_id: str
    status: str
    next_step: str
    created_at: str
    updated_at: str
    user_id: str
    plan_code: str
    plan_name: str
    price_cents: int
    currency: str
    billing_period: str
    customer_name: str
    customer_email: str
    customer_whatsapp: str
    customer_document: str | None = None
    customer_notes: str | None = None
    payment_link: str | None = None
    payment_link_sent_at: str | None = None
    released_at: str | None = None


class AdminCheckoutIntentListResponse(BaseModel):
    items: list[AdminCheckoutIntentItem]
    total: int
    limit: int
    offset: int


class AdminCheckoutIntentEventItem(BaseModel):
    event_id: str
    intent_id: str
    event_type: str
    event_message: str
    actor_kind: str
    actor_user_id: str | None = None
    payload_json: str | None = None
    created_at: str


class AdminCheckoutIntentHistoryResponse(BaseModel):
    intent_id: str
    items: list[AdminCheckoutIntentEventItem]


class AdminUserItem(BaseModel):
    user_id: str
    name: str
    email: str
    is_admin: bool
    created_at: str
    updated_at: str


class AdminUserListResponse(BaseModel):
    items: list[AdminUserItem]
    total: int
    limit: int
    offset: int


class AdminSetUserRoleRequest(BaseModel):
    user_id: str
    is_admin: bool


class AdminUserRoleEventItem(BaseModel):
    event_id: str
    target_user_id: str
    target_email: str
    event_type: str
    actor_user_id: str | None = None
    actor_email: str | None = None
    previous_is_admin: bool
    new_is_admin: bool
    created_at: str


class AdminUserRoleHistoryResponse(BaseModel):
    user_id: str
    items: list[AdminUserRoleEventItem]


class ClientConversionItem(BaseModel):
    processing_id: str
    created_at: str
    filename: str
    model: str
    conversion_type: str
    status: str
    transactions_count: int | None = None
    pages_count: int | None = None


class ClientConversionsResponse(BaseModel):
    items: list[ClientConversionItem]


class ContactResponse(BaseModel):
    status: str
    delivery_mode: str
    provider_message_id: str | None = None
