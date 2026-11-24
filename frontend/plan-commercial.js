(function () {
  "use strict";
  const blackCodes = new Set(["essencial_black", "profissional_black", "black_friday_3_meses"]);
  window.OfxPlanCommercial = {
    isBlack: function (plan) { return blackCodes.has(plan.code || plan.plan_code); },
    suffix: function (plan) { return plan.billing_period === "prepaid_quarter" ? " pelos 3 meses" : "/mês"; },
    terms: function (plan) {
      if (plan.billing_period === "prepaid_quarter") {
        return "Pagamento único. 1.000 páginas por mês durante 3 meses a partir da ativação, sem renovação automática.";
      }
      return blackCodes.has(plan.code || plan.plan_code)
        ? "Preço e franquia mantidos nas renovações enquanto você permanecer neste plano."
        : "";
    },
  };
})();
