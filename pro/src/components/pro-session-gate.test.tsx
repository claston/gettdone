import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { expireProSession, forgetProSession } from "@/data/pro-session-cache";

import { ProSessionGate } from "./pro-session-gate";

afterEach(() => forgetProSession());

describe("ProSessionGate", () => {
  it("encerra a visualização protegida quando a renovação confirma uma sessão expirada", async () => {
    const authService = {
      currentUser: vi.fn().mockResolvedValue({
        user_id: "usr-1",
        name: "Canário",
        email: "canario@example.com",
      }),
      login: vi.fn(),
      logout: vi.fn(),
    };
    render(
      <ProSessionGate authService={authService}>
        <div>Dados da sessão</div>
      </ProSessionGate>,
    );
    await screen.findByText("Dados da sessão");
    act(() => expireProSession());
    expect(
      screen.getByRole("heading", { name: "Entre no OFX Simples PRO" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Dados da sessão")).not.toBeInTheDocument();
  });
  it("solicita login quando o visitante ainda é anônimo", async () => {
    const authService = {
      currentUser: vi.fn().mockResolvedValue(null),
      login: vi.fn(),
      logout: vi.fn(),
    };

    render(
      <ProSessionGate authService={authService}>
        <div>Upload autenticado</div>
      </ProSessionGate>,
    );

    expect(
      await screen.findByRole("heading", { name: "Entre no OFX Simples PRO" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Upload autenticado")).not.toBeInTheDocument();
  });

  it("libera o upload depois que o backend cria a sessão HttpOnly", async () => {
    const authService = {
      currentUser: vi.fn().mockResolvedValue(null),
      login: vi.fn().mockResolvedValue({
        user_id: "usr-1",
        name: "Usuário Canário",
        email: "canario@example.com",
        plan_code: "paid",
      }),
      logout: vi.fn(),
    };

    render(
      <ProSessionGate authService={authService}>
        <div>Upload autenticado</div>
      </ProSessionGate>,
    );
    await screen.findByRole("heading", { name: "Entre no OFX Simples PRO" });

    fireEvent.change(screen.getByLabelText("E-mail"), {
      target: { value: "canario@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Senha"), {
      target: { value: "senha-segura" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Entrar" }));

    await waitFor(() =>
      expect(authService.login).toHaveBeenCalledWith(
        "canario@example.com",
        "senha-segura",
      ),
    );
    expect(await screen.findByText("Upload autenticado")).toBeInTheDocument();
  });
});
