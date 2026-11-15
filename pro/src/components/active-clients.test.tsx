import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { ReceivedStatementsPage } from "./received-statements-page";

const client = {
  id: "c1",
  name: "Cliente ativo",
  tax_id: null,
  trade_name: "",
  updated_at: "v1",
  historical_count: 2,
  available_count: 1,
  expired_count: 1,
  attention_count: 0,
  accounts: [],
};
const page = {
  totals: { statements: 1, ready: 1, attention: 0 },
  clients: [client],
  pending: [],
  next_cursor: null,
  all_client_count: 2,
  active_client_count: 1,
};
afterEach(() => vi.useRealTimers());

it("abre ativos por padrão e consulta histórico completo ao trocar de aba", async () => {
  const service = {
    list: vi
      .fn()
      .mockImplementation((_c, _s, _p, _q, active) =>
        Promise.resolve({
          ...page,
          clients: active
            ? [client]
            : [
                client,
                {
                  ...client,
                  id: "c2",
                  name: "Cliente expirado",
                  available_count: 0,
                },
              ],
        }),
      ),
    refresh: vi.fn().mockResolvedValue(undefined),
    edit: vi.fn(),
    confirm: vi.fn(),
  };
  render(<ReceivedStatementsPage service={service} />);
  const active = await screen.findByRole("tab", {
    name: "Com conversões ativas (1)",
  });
  expect(active).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByText("Cliente expirado")).not.toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Atualizar extratos" }),
    ).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole("tab", { name: "Identificados (2)" }));
  expect(await screen.findByText("Cliente expirado")).toBeVisible();
  expect(screen.getByText("Nenhuma conversão ativa")).toBeVisible();
  expect(service.list).toHaveBeenLastCalledWith(
    undefined,
    expect.any(AbortSignal),
    undefined,
    "",
    false,
  );
  expect(service.refresh).toHaveBeenCalledTimes(1);
});

it("atualiza no instante de expiração usando o relógio do servidor e cancela ao desmontar", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2030-01-01T00:00:00Z"));
  let expired = false;
  const service = {
    list: vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          expired
            ? {
                ...page,
                clients: [],
                active_client_count: 0,
                next_expiration_at: null,
              }
            : {
                ...page,
                server_time: "2026-11-15T12:00:00Z",
                next_expiration_at: "2026-11-15T12:00:01Z",
              },
        ),
      ),
    refresh: vi.fn().mockResolvedValue(undefined),
    edit: vi.fn(),
    confirm: vi.fn(),
  };
  const rendered = render(<ReceivedStatementsPage service={service} />);
  await act(async () => {});
  expect(screen.getByText("Cliente ativo")).toBeVisible();
  expired = true;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1250);
  });
  expect(screen.queryByText("Cliente ativo")).not.toBeInTheDocument();
  expect(
    screen.getByText("Nenhum cliente com conversões ativas no momento."),
  ).toBeVisible();
  expect(service.refresh).toHaveBeenCalledTimes(1);
  rendered.unmount();
  const calls = service.list.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(60_000);
  });
  expect(service.list).toHaveBeenCalledTimes(calls);
});
