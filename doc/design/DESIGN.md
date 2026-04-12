# Design System Specification: The Financial Architect

## 1. Overview & Creative North Star
**Creative North Star: "The Precision Vault"**

This design system moves away from the "boxy" nature of traditional fintech. In financial reconciliation, the user’s cognitive load is high; our goal is to provide a sense of "Air and Authority." We achieve this through **Editorial Minimalism**—treating financial data with the same elegance as a high-end fashion magazine. 

By leveraging intentional asymmetry, expansive negative space, and tonal layering, we break the "template" look. We do not use borders to contain data; we use gravity and light. The interface should feel like a single, continuous sheet of high-grade digital paper, folded and layered to reveal information only when necessary.

---

## 2. Colors & Surface Logic

The palette is rooted in a "High-Value Neutral" base with a primary "Deep Trust Blue" (`#003ec7`).

### The "No-Line" Rule
**Explicit Instruction:** Designers are prohibited from using 1px solid borders for sectioning or layout containment. Boundaries must be defined solely through background color shifts or subtle tonal transitions. 
- Use `surface-container-low` for secondary sidebar areas.
- Use `surface-container-lowest` (#ffffff) for the primary "action" canvas.
- Separation is achieved via **8px to 16px of negative space**, not a line.

### Surface Hierarchy & Nesting
Treat the UI as a series of physical layers. 
1.  **Base:** `surface` (#f8f9fa) - The foundation.
2.  **Sections:** `surface-container-low` (#f3f4f5) - Large layout blocks.
3.  **Active Elements:** `surface-container-lowest` (#ffffff) - Cards and interactive data tables.
4.  **Overlays:** Glassmorphism using `surface_variant` with 60% opacity and a `20px` backdrop-blur.

### Signature Textures (The "Glass & Gradient" Rule)
To avoid a flat, "Bootstrap" feel:
- **CTAs:** Use a subtle linear gradient from `primary` (#003ec7) to `primary_container` (#0052ff) at a 135-degree angle. This adds "visual soul."
- **Hero Accents:** Employ a 0.5px "inner glow" (box-shadow: inset 0 1px 0 0 rgba(255,255,255,0.2)) on primary buttons to mimic a physical glass edge.

---

## 3. Typography: The Editorial Scale

We utilize two typefaces to balance character with utility: **Manrope** (Display/Headlines) for an authoritative, geometric feel, and **Inter** (UI/Body) for maximum legibility in dense data.

*   **The Authority (Manrope):** Use `display-lg` and `headline-md` for high-level summaries. Large font sizes combined with `on_surface` color create a "Statement" look that inspires confidence.
*   **The Utility (Inter):** All tabular data and labels use Inter. `label-md` is the workhorse for financial line items—ensure it uses `on_surface_variant` (#434656) to maintain a hierarchy that doesn't compete with primary figures.
*   **Contrast as Navigation:** Use `title-lg` in Semi-Bold to anchor sections. The jump from a `display-sm` (2.25rem) header to a `body-md` (0.875rem) description creates the "Editorial" rhythm.

---

## 4. Elevation & Depth: Tonal Layering

Traditional shadows are often "dirty." We use **Ambient Shadows** and **Tonal Stacking**.

*   **The Layering Principle:** Place a `surface-container-lowest` card on a `surface-container-low` background. This creates a "soft lift" that feels architectural.
*   **Ambient Shadows:** If a card must float (e.g., a modal or a primary reconciliation tool), use a multi-layered shadow:
    *   `0 4px 6px -1px rgba(0, 62, 199, 0.03), 0 10px 15px -3px rgba(0, 0, 0, 0.05)`
    *   *Note the slight tint of `primary` in the shadow to keep the colors "alive."*
*   **The "Ghost Border" Fallback:** If accessibility requires a container (e.g., in high-contrast modes), use `outline_variant` at **15% opacity**. Never use a 100% opaque border.
*   **Corner Radii:** Maintain a strict `xl` (1.5rem) for main dashboard containers and `md` (0.75rem) for nested elements like input fields and buttons.

---

## 5. Components: Precision Primitives

### Buttons & Chips
*   **Primary Button:** `primary` gradient, `xl` roundedness. No border. Text is `on_primary` (#ffffff).
*   **Secondary/Ghost:** `surface-container-high` background. On hover, transition to `surface-container-highest`.
*   **Status Chips:** Use `secondary_container` for "Matched" (Green) and `error_container` for "Discrepancy" (Red). Keep the `on-container` text color high-contrast for readability.

### Financial Data Cards
*   **Forbid Dividers:** Do not use lines between rows. Use `surface-container-low` hover states to highlight a row.
*   **Vertical Spacing:** Use the `xl` (1.5rem) spacing token between card sections to let the data breathe.

### Input Fields
*   **State:** Default state should be `surface-container-lowest` with a "Ghost Border" (`outline-variant` @ 20%).
*   **Focus:** Transition the border to 100% `primary` opacity and add a 4px `primary_fixed` outer glow.

### Reconciliation Progress (New Component)
*   **The "Pulse" Bar:** A custom progress component using a `secondary` (#006c49) track with a blurred glow effect, signifying a "healthy" financial state.

---

## 6. Do’s and Don'ts

### Do:
*   **Use Asymmetry:** Place large headline text on the left with a smaller, high-density data visualization on the right to create a "Dashboard Editorial" feel.
*   **Respect White Space:** If you think there is enough padding, add 8px more. Financial tools fail when they feel "cramped."
*   **Use Subtle Blurs:** Use `24px` backdrop blurs on navigation bars to allow the brand colors to bleed through as the user scrolls.

### Don’t:
*   **Don't Use Pure Black:** Use `on_surface` (#191c1d) for text. Pure black is too harsh for a professional "Vault" aesthetic.
*   **Don't Use Lines:** If you find yourself reaching for a `<hr>` or a `border-bottom`, stop. Use a `16px` gap or a background color shift instead.
*   **Don't Over-Animate:** Movement should be "Stiff & Premium"—use `cubic-bezier(0.16, 1, 0.3, 1)` for all transitions. No bouncing.