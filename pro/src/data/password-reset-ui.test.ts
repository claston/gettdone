import { readFileSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const frontend = path.resolve(process.cwd(), "../frontend");
const script = readFileSync(path.join(frontend, "password-reset.js"), "utf8");

function setup(page: string, hash = "") {
  window.history.replaceState(null, "", `/${page}${hash}`);
  document.documentElement.innerHTML = readFileSync(
    path.join(frontend, page),
    "utf8",
  );
  const fetchMock = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({ message: "Solicitação concluída." }),
  });
  vi.stubGlobal("fetch", fetchMock);
  window.eval(script);
  return fetchMock;
}

function submit(id: string) {
  document
    .getElementById(id)!
    .dispatchEvent(new Event("submit", { cancelable: true }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("Recuperação de senha", () => {
  it("solicita por e-mail e evita envios repetidos durante o intervalo", async () => {
    vi.useFakeTimers();
    const fetchMock = setup("esqueci-senha.html");
    (document.getElementById("email") as HTMLInputElement).value =
      "user@example.com";
    submit("forgot-password-form");
    submit("forgot-password-form");
    await vi.advanceTimersByTimeAsync(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(document.getElementById("status-msg")).toHaveTextContent(
      "Solicitação concluída.",
    );
    expect(document.getElementById("submit-btn")).toBeDisabled();
    await vi.advanceTimersByTimeAsync(60000);
    expect(document.getElementById("submit-btn")).not.toBeDisabled();
  });

  it("remove o token da URL, verifica confirmação e consome o link apenas ao salvar", async () => {
    const fetchMock = setup("redefinir-senha.html", "#token=secret-token");
    expect(window.location.hash).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
    (document.getElementById("password") as HTMLInputElement).value =
      "new-password";
    (document.getElementById("confirm-password") as HTMLInputElement).value =
      "different-password";
    submit("reset-password-form");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(document.getElementById("status-msg")).toHaveTextContent(
      "As senhas não coincidem",
    );
    (document.getElementById("confirm-password") as HTMLInputElement).value =
      "new-password";
    submit("reset-password-form");
    await vi.waitFor(() =>
      expect(document.getElementById("status-msg")).toHaveTextContent(
        "Solicitação concluída.",
      ),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/auth/reset-password"),
      expect.objectContaining({
        body: JSON.stringify({
          token: "secret-token",
          password: "new-password",
        }),
      }),
    );
    expect(document.getElementById("reset-password-form")).not.toBeVisible();
    expect(
      (document.getElementById("password") as HTMLInputElement).value,
    ).toBe("");
  });

  it("explica como recuperar um link ausente", () => {
    const fetchMock = setup("redefinir-senha.html");
    expect(document.getElementById("reset-password-form")).not.toBeVisible();
    expect(document.getElementById("status-msg")).toHaveTextContent(
      "Solicite um novo link",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("permite tentar novamente após uma falha de rede", async () => {
    const fetchMock = setup("esqueci-senha.html");
    fetchMock.mockRejectedValue(new Error("Network unavailable"));
    submit("forgot-password-form");
    await vi.waitFor(() =>
      expect(document.getElementById("status-msg")).toHaveTextContent(
        "Verifique sua conexão",
      ),
    );
    expect(document.getElementById("submit-btn")).not.toBeDisabled();
  });

  it("mostra o erro de link expirado sem inserir HTML recebido", async () => {
    const fetchMock = setup("redefinir-senha.html", "#token=expired-token");
    fetchMock.mockResolvedValue({
      ok: false,
      json: async () => ({ detail: "Link expirado. <b>Solicite outro.</b>" }),
    });
    (document.getElementById("password") as HTMLInputElement).value =
      "new-password";
    (document.getElementById("confirm-password") as HTMLInputElement).value =
      "new-password";
    submit("reset-password-form");
    await vi.waitFor(() =>
      expect(document.getElementById("status-msg")).toHaveTextContent(
        "Link expirado.",
      ),
    );
    expect(document.querySelector("#status-msg b")).toBeNull();
    expect(document.getElementById("submit-btn")).not.toBeDisabled();
  });
});
