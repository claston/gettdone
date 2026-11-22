import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { FiscalWorkspace } from "./fiscal-workspace";
import {
  fiscalCommand,
  loadFiscalWorkspace,
  uploadFiscalNote,
  type FiscalUploadReceipt,
} from "@/data/fiscal-service";

vi.mock("@/data/fiscal-service", () => ({
  loadFiscalWorkspace: vi.fn(),
  fiscalCommand: vi.fn(),
  uploadFiscalNote: vi.fn(),
  downloadFiscal: vi.fn(),
}));
const fixture = {
  features: { invoices: true, taxes: true, revenue: true },
  documents: [],
  imports: [],
  links: [],
  suggestions: [],
  categories: {
    unclassified: "Não classificado",
    tax_simples: "Simples Nacional / DAS",
    operating_receipt: "Recebimento operacional",
  },
  movements: [
    {
      id: "m1",
      date: "2026-11-02",
      amount: "-50.00",
      currency: "BRL",
      description: "PAGAMENTO DAS",
      version: 1,
      possible_duplicate: false,
      classification: null,
      tax_suggestion: {
        category: "tax_simples",
        evidence: "Descrição bancária",
      },
      source: { job_id: "j1", batch_id: "b1", filename: "extrato.pdf" },
    },
  ],
  movement_count: 1,
  next_offset: null,
  access_expires_at: null,
  truncated: false,
  summary: {
    documented_sales: "100.00",
    operating_receipts: "80.00",
    unclassified_inflows: "20.00",
    excluded_inflows: "0.00",
    tax_payments: {},
    pending_documents: 0,
    coverage: "unknown",
    consolidation: "observed",
    basis: "Notas por emissão",
  },
};
beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(loadFiscalWorkspace).mockResolvedValue(fixture);
  vi.mocked(fiscalCommand).mockResolvedValue({});
  vi.mocked(uploadFiscalNote).mockReset();
});
it("mostra o envio imediatamente e acompanha o arquivo recebido", async () => {
  let finish!: (receipt: FiscalUploadReceipt) => void;
  vi.mocked(uploadFiscalNote).mockReturnValue(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  const input = await screen.findByLabelText("Importar nota XML ou PDF");
  fireEvent.change(input, {
    target: { files: [new File(["<NFe/>"], "arroz.xml")] },
  });
  expect(await screen.findByText("Enviando arroz.xml…")).toBeInTheDocument();
  expect(input).toBeDisabled();
  expect(screen.queryByText(/Nenhuma nota importada/)).toBeNull();
  vi.mocked(loadFiscalWorkspace).mockResolvedValue({
    ...fixture,
    imports: [
      { id: "i1", filename: "arroz.xml", state: "queued", error: null },
    ],
  });
  await act(async () => finish({ id: "i1", state: "queued" }));
  expect(
    await screen.findByText(
      "Arquivo arroz.xml recebido. Aguardando a leitura da nota.",
    ),
  ).toBeInTheDocument();
  expect(
    await screen.findByLabelText("Importar nota XML ou PDF"),
  ).toBeEnabled();
});

it("oferece importação e revisão documental sem os controles de conciliação", async () => {
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
      documentsOnly
    />,
  );
  expect(
    await screen.findByLabelText("Importar nota XML ou PDF"),
  ).toBeEnabled();
  expect(
    screen.getByRole("heading", { name: "Notas fiscais" }),
  ).toBeInTheDocument();
  expect(
    screen.queryByRole("heading", { name: "Vínculos registrados" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Exportar conferência CSV" }),
  ).not.toBeInTheDocument();
});

it.each(["ready", "failed"])(
  "consulta sem desmontar a tela e para ao receber %s",
  async (state) => {
    vi.useFakeTimers();
    try {
      const pending = {
        ...fixture,
        imports: [
          { id: "i1", filename: "nota.xml", state: "queued", error: null },
        ],
      };
      vi.mocked(loadFiscalWorkspace).mockResolvedValue(pending);
      await act(async () => {
        render(
          <FiscalWorkspace
            clientId="c1"
            month="2026-07"
            account=""
            mode="invoices"
          />,
        );
      });
      const input = screen.getByLabelText("Importar nota XML ou PDF");
      let finish!: (result: typeof pending) => void;
      vi.mocked(loadFiscalWorkspace).mockReturnValueOnce(
        new Promise((resolve) => {
          finish = resolve;
        }),
      );
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
      expect(loadFiscalWorkspace).toHaveBeenCalledTimes(2);
      expect(input).toBeInTheDocument();
      expect(
        screen.queryByText("Consultando documentos e movimentos…"),
      ).toBeNull();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(12000);
      });
      expect(loadFiscalWorkspace).toHaveBeenCalledTimes(2);
      await act(async () =>
        finish({ ...pending, imports: [{ ...pending.imports[0], state }] }),
      );
      expect(input).toBeInTheDocument();
      expect(
        screen.getByText(
          state === "ready"
            ? "Nota nota.xml importada."
            : /Não foi possível ler a nota nota.xml/,
        ),
      ).toBeInTheDocument();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(120000);
      });
      expect(loadFiscalWorkspace).toHaveBeenCalledTimes(2);
      expect(uploadFiscalNote).not.toHaveBeenCalled();
      expect(fiscalCommand).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  },
);

it("limita a espera automática e só retoma a consulta por Atualizar", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(loadFiscalWorkspace).mockResolvedValue({
      ...fixture,
      imports: [
        { id: "i1", filename: "nota.xml", state: "queued", error: null },
      ],
    });
    await act(async () => {
      render(
        <FiscalWorkspace
          clientId="c1"
          month="2026-07"
          account=""
          mode="invoices"
        />,
      );
    });
    for (let i = 0; i < 30; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(4000);
      });
    }
    expect(
      screen.getByText(/A consulta automática foi pausada/),
    ).toBeInTheDocument();
    const count = vi.mocked(loadFiscalWorkspace).mock.calls.length;
    await act(async () => {
      fireEvent.focus(window);
      await vi.advanceTimersByTimeAsync(120000);
    });
    expect(loadFiscalWorkspace).toHaveBeenCalledTimes(count);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    });
    expect(loadFiscalWorkspace).toHaveBeenCalledTimes(count + 1);
    expect(screen.queryByText(/A consulta automática foi pausada/)).toBeNull();
    expect(uploadFiscalNote).not.toHaveBeenCalled();
  } finally {
    vi.useRealTimers();
  }
});

it("encerra uma consulta sem resposta, ignora resposta tardia e permite consultar novamente", async () => {
  vi.useFakeTimers();
  try {
    let finish!: (result: typeof fixture) => void;
    vi.mocked(loadFiscalWorkspace).mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    await act(async () => {
      render(
        <FiscalWorkspace
          clientId="c1"
          month="2026-07"
          account=""
          mode="invoices"
        />,
      );
    });
    const signal = vi.mocked(loadFiscalWorkspace).mock.calls[0][2];
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(signal.aborted).toBe(true);
    expect(screen.getByRole("alert")).toHaveTextContent(
      "A consulta demorou mais que o esperado",
    );
    await act(async () => finish(fixture));
    expect(screen.queryByLabelText("Importar nota XML ou PDF")).toBeNull();
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
    });
    expect(loadFiscalWorkspace).toHaveBeenCalledTimes(2);
    expect(
      screen.getByLabelText("Importar nota XML ou PDF"),
    ).toBeInTheDocument();
  } finally {
    vi.useRealTimers();
  }
});

it("não repete imediatamente a consulta quando o servidor retorna acesso já expirado", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(loadFiscalWorkspace).mockResolvedValue({
      ...fixture,
      access_expires_at: new Date(Date.now() - 1000).toISOString(),
    });
    await act(async () => {
      render(
        <FiscalWorkspace
          clientId="c1"
          month="2026-07"
          account=""
          mode="invoices"
        />,
      );
    });
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
    }
    expect(loadFiscalWorkspace).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("alert")).toHaveTextContent("Atualize a lista");
    expect(screen.queryByLabelText("Importar nota XML ou PDF")).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});

it("cancela a consulta ao trocar de empresa e ao sair, sem aplicar a resposta antiga", async () => {
  let finish!: (result: typeof fixture) => void;
  vi.mocked(loadFiscalWorkspace).mockReturnValueOnce(
    new Promise((resolve) => {
      finish = resolve;
    }),
  );
  const view = render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-07"
      account=""
      mode="invoices"
    />,
  );
  const oldSignal = vi.mocked(loadFiscalWorkspace).mock.calls[0][2];
  view.rerender(
    <FiscalWorkspace
      clientId="c2"
      month="2026-07"
      account=""
      mode="invoices"
    />,
  );
  expect(oldSignal.aborted).toBe(true);
  await screen.findByLabelText("Importar nota XML ou PDF");
  await act(async () =>
    finish({
      ...fixture,
      movements: [
        { ...fixture.movements[0], description: "DADOS DA EMPRESA ANTERIOR" },
      ],
    }),
  );
  expect(screen.queryByText("DADOS DA EMPRESA ANTERIOR")).toBeNull();
  vi.mocked(loadFiscalWorkspace).mockReturnValueOnce(new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Atualizar" }));
  const lastSignal = vi.mocked(loadFiscalWorkspace).mock.lastCall![2];
  view.unmount();
  expect(lastSignal.aborted).toBe(true);
});
it("mostra erro de envio e permite selecionar o mesmo arquivo novamente", async () => {
  vi.mocked(uploadFiscalNote).mockRejectedValue(
    new Error("Não foi possível enviar a nota."),
  );
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  const input = await screen.findByLabelText("Importar nota XML ou PDF");
  fireEvent.change(input, {
    target: { files: [new File(["<NFe/>"], "arroz.xml")] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Não foi possível enviar a nota.",
  );
  expect(input).toBeEnabled();
  expect(input).toHaveValue("");
  expect(screen.queryByText(/Nota arroz.xml importada/)).toBeNull();
});
it("continua mostrando uma nota concluída ao voltar à tela, mesmo emitida em outro mês", async () => {
  vi.mocked(loadFiscalWorkspace).mockResolvedValue({
    ...fixture,
    imports: [
      {
        id: "i1",
        filename: "arroz.xml",
        state: "ready",
        error: null,
        document_id: "n1",
      },
    ],
    documents: [
      {
        id: "n1",
        version: 1,
        kind: "nfe",
        number: "901",
        series: "1",
        issued_on: "2027-03-10",
        competence_on: null,
        gross_amount: "100.00",
        net_amount: null,
        operation: "sale",
        status: "reported",
        direction: "received",
        issuer: { name: "Fornecedor", tax_id: null },
        recipient: { name: "Empresa", tax_id: null },
        taxes: {},
        review_required: true,
        source: { format: "xml", fields: {} },
      },
    ],
  });
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  expect(
    await screen.findByText("Nota arroz.xml importada."),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Ver nota importada" }));
  await waitFor(() =>
    expect(loadFiscalWorkspace).toHaveBeenLastCalledWith(
      "c1",
      expect.stringContaining("document_id=n1"),
      expect.any(AbortSignal),
    ),
  );
  expect(await screen.findByLabelText("Nota fiscal")).toHaveValue("n1");
});
it("informa a falha de leitura sem exibir confirmação de importação", async () => {
  vi.mocked(loadFiscalWorkspace).mockResolvedValue({
    ...fixture,
    imports: [
      {
        id: "i1",
        filename: "arroz.xml",
        state: "failed",
        error: "XML inválido.",
      },
    ],
  });
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Não foi possível ler a nota arroz.xml. XML inválido.",
  );
  expect(
    screen.queryByRole("button", { name: "Ver nota importada" }),
  ).toBeNull();
  vi.mocked(uploadFiscalNote).mockRejectedValue(
    new Error("Este arquivo já foi enviado, mas não pôde ser processado."),
  );
  fireEvent.change(screen.getByLabelText("Importar nota XML ou PDF"), {
    target: { files: [new File(["<NFe/>"], "arroz.xml")] },
  });
  expect(
    await screen.findByText("arroz.xml: XML inválido."),
  ).toBeInTheDocument();
  expect(screen.getByRole("alert")).toHaveTextContent(
    "não pôde ser processado",
  );
});
it("encerra um envio sem resposta e orienta conferir antes de repetir", async () => {
  // Even a session bootstrap that does not settle must not leave the UI stuck.
  vi.mocked(uploadFiscalNote).mockReturnValue(new Promise(() => {}));
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  const input = await screen.findByLabelText("Importar nota XML ou PDF");
  vi.useFakeTimers();
  try {
    fireEvent.change(input, {
      target: { files: [new File(["<NFe/>"], "arroz.xml")] },
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(120000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "O envio demorou mais que o esperado.",
    );
    expect(input).toBeEnabled();
  } finally {
    vi.useRealTimers();
  }
});
it("mantém a expiração mesmo se a janela de seleção interromper a consulta periódica", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(loadFiscalWorkspace).mockResolvedValue({
      ...fixture,
      access_expires_at: new Date(Date.now() + 5000).toISOString(),
      imports: [
        { id: "i1", filename: "anterior.xml", state: "queued", error: null },
      ],
    });
    await act(async () => {
      render(
        <FiscalWorkspace
          clientId="c1"
          month="2026-11"
          account=""
          mode="invoices"
        />,
      );
    });
    const input = screen.getByLabelText("Importar nota XML ou PDF");
    fireEvent.click(input);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4000);
    });
    expect(input).toBeInTheDocument();
    expect(loadFiscalWorkspace).toHaveBeenCalledTimes(1);
    vi.mocked(loadFiscalWorkspace).mockRejectedValue(
      new Error("Acesso expirado"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Acesso expirado");
    expect(input).not.toBeInTheDocument();
    expect(screen.queryByText(/anterior.xml/)).toBeNull();
  } finally {
    vi.useRealTimers();
  }
});
it("mantém a sugestão tributária pendente até confirmação", async () => {
  render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("PAGAMENTO DAS");
  expect(fiscalCommand).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Categoria de PAGAMENTO DAS"), {
    target: { value: "tax_simples" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirmar classificação" }),
  );
  await waitFor(() =>
    expect(fiscalCommand).toHaveBeenCalledWith(
      "c1",
      "/classifications/m1",
      expect.objectContaining({
        category: "tax_simples",
        expected_version: 0,
        movement_version: 1,
      }),
    ),
  );
});
it("separa vendas documentadas e recebimentos sem somá-los", async () => {
  render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="revenue" />,
  );
  await screen.findByText("Vendas documentadas");
  expect(
    screen.getByText("Recebimentos operacionais identificados"),
  ).toBeInTheDocument();
  expect(screen.getByText(/cobertura/i)).toBeInTheDocument();
  expect(screen.queryByText(/180,00/)).not.toBeInTheDocument();
});
it("remove os valores ao trocar para um cliente cujo acesso falha", async () => {
  const view = render(
    <FiscalWorkspace clientId="c1" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("PAGAMENTO DAS");
  vi.mocked(loadFiscalWorkspace).mockRejectedValue(
    new Error("Acesso expirado"),
  );
  view.rerender(
    <FiscalWorkspace clientId="c2" month="2026-11" account="" mode="taxes" />,
  );
  await screen.findByText("Acesso expirado");
  expect(screen.queryByText("PAGAMENTO DAS")).not.toBeInTheDocument();
});

it("vincula a nota somente depois de conferir e confirmar a sugestão", async () => {
  vi.mocked(loadFiscalWorkspace).mockResolvedValue({
    ...fixture,
    documents: [
      {
        id: "n1",
        version: 1,
        kind: "nfe",
        number: "123",
        series: "1",
        issued_on: "2026-11-01",
        competence_on: null,
        gross_amount: "50.00",
        net_amount: null,
        operation: "sale",
        status: "reported",
        direction: "received",
        issuer: { name: "Fornecedor", tax_id: "12345678000195" },
        recipient: { name: "Empresa", tax_id: "98765432000198" },
        taxes: {},
        review_required: false,
        source: { format: "xml", fields: {} },
      },
    ],
    suggestions: [
      {
        movement_id: "m1",
        movement_version: 1,
        amount: "50.00",
        confidence: "weak",
        evidence: ["amount", "date_window"],
        movement: fixture.movements[0],
      },
    ],
  });
  render(
    <FiscalWorkspace
      clientId="c1"
      month="2026-11"
      account=""
      mode="invoices"
    />,
  );
  fireEvent.change(await screen.findByLabelText("Nota fiscal"), {
    target: { value: "n1" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Conferir vínculo" }),
  );
  expect(fiscalCommand).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Confirmar vínculo" }));
  await waitFor(() =>
    expect(fiscalCommand).toHaveBeenCalledWith(
      "c1",
      "/fiscal/links",
      expect.objectContaining({
        document_id: "n1",
        movement_id: "m1",
        amount: "50.00",
        document_version: 1,
        movement_version: 1,
      }),
    ),
  );
});
