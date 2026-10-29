// Internal acceptance builds can still exercise the preserved legacy screens.
// Public PRO builds hide these screens; this flag never grants API permissions.
export const legacyOrganizationVisible =
  process.env.PRO_INTERNAL_LEGACY_ORGANIZATION_UI === "true";
