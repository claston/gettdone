// Internal acceptance builds can still exercise the preserved legacy screens.
// Public PRO builds hide these screens; this flag never grants API permissions.
export const legacyOrganizationVisible =
  process.env.PRO_INTERNAL_LEGACY_ORGANIZATION_UI === "true";

// Server-only opt-in. This exposes synthetic demos, never financial permissions.
export const financialCenterDemoVisible =
  process.env.PRO_FINANCIAL_CENTER_DEMO === "true";
