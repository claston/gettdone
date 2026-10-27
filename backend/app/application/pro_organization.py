"""Manual organization metadata; never submits jobs or changes quota/ownership."""

import re
from datetime import date, datetime, timezone
from uuid import uuid4

from app.application.access_control.access_control_db import AccessControlDbComponent


class OrganizationConflict(ValueError):
    pass


class OrganizationService:
    def __init__(self, db: AccessControlDbComponent) -> None:
        self.db = db

    def _write(self, conn, query: str, params: tuple = ()) -> int:
        cursor = self.db.execute(conn, query, params)
        try:
            return cursor.rowcount
        finally:
            cursor.close()

    def _organization(self, conn, owner: str, *, create: bool = False) -> str | None:
        if create:
            self._write(
                conn,
                """INSERT INTO pro_organizations (id, owner_user_id, created_at) VALUES (?, ?, ?)
                        ON CONFLICT (owner_user_id) DO NOTHING""",
                (f"org_{uuid4().hex}", owner, self._now()),
            )
        row = self.db.fetchone(conn, "SELECT id FROM pro_organizations WHERE owner_user_id = ?", (owner,))
        return row["id"] if row else None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _text(value: str, *, limit: int = 160, required: bool = True) -> str:
        normalized = value.strip()
        if (required and not normalized) or len(normalized) > limit:
            raise ValueError("Confira os campos obrigatórios e seus limites de tamanho.")
        return normalized

    def _entity(self, conn, table: str, organization: str, entity_id: str) -> dict:
        row = self.db.fetchone(conn, f"SELECT * FROM {table} WHERE organization_id = ? AND id = ?", (organization, entity_id))
        if row is None:
            raise KeyError("Registro não encontrado.")
        return dict(row)

    @staticmethod
    def _public(row: dict) -> dict:
        return {key: value for key, value in row.items() if key not in {"organization_id", "created_by", "updated_by"}}

    def catalog(self, owner: str) -> dict:
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            if organization is None:
                return {"clients": [], "accounts": [], "periods": []}
            return {
                key: [
                    self._public(dict(row))
                    for row in self.db.fetchall(
                        conn,
                        f"SELECT * FROM pro_{key} WHERE organization_id = ? ORDER BY name, id",
                        (organization,),
                    )
                ]
                for key in ("clients", "accounts", "periods")
            }

    def _save(self, owner: str, kind: str, values: dict, entity_id: str | None) -> dict:
        table = {"clients": "pro_clients", "accounts": "pro_accounts", "periods": "pro_periods"}[kind]
        now = self._now()
        with self.db.connect() as conn:
            organization = self._organization(conn, owner, create=True)
            existing = self._entity(conn, table, organization, entity_id) if entity_id else None
            if kind == "clients" and values["tax_id"]:
                duplicate = self.db.fetchone(
                    conn, "SELECT id FROM pro_clients WHERE organization_id = ? AND tax_id = ?", (organization, values["tax_id"])
                )
                if duplicate is not None and duplicate["id"] != entity_id:
                    raise ValueError("Já existe um cliente com este CPF/CNPJ.")
            if kind == "accounts":
                self._entity(conn, "pro_clients", organization, values["client_id"])
                if existing and existing["client_id"] != values["client_id"]:
                    used = self.db.fetchone(
                        conn,
                        "SELECT job_id FROM pro_conversion_contexts WHERE organization_id = ? AND account_id = ? LIMIT 1",
                        (organization, entity_id),
                    )
                    if used:
                        raise ValueError("Esta conta já possui conversões vinculadas. Mantenha o cliente ou cadastre outra conta.")
            if entity_id:
                self._write(
                    conn,
                    f"UPDATE {table} SET {', '.join(f'{key} = ?' for key in values)}, updated_at = ? WHERE organization_id = ? AND id = ?",
                    (*values.values(), now, organization, entity_id),
                )
            else:
                entity_id = f"{kind[:-1]}_{uuid4().hex}"
                columns = ("id", "organization_id", *values, "created_by", "created_at", "updated_at")
                self._write(
                    conn,
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    (entity_id, organization, *values.values(), owner, now, now),
                )
            result = self._public(self._entity(conn, table, organization, entity_id))
            conn.commit()
            return result

    def save_client(self, owner: str, *, name: str, tax_id: str | None = None, trade_name: str = "", entity_id: str | None = None) -> dict:
        normalized_tax = re.sub(r"\D", "", tax_id or "") or None
        if tax_id and tax_id.strip() and (
            len(normalized_tax or "") not in (11, 14) or not re.fullmatch(r"[\d./\s-]+", tax_id)
        ):
            raise ValueError("Informe CPF com 11 dígitos ou CNPJ com 14 dígitos.")
        return self._save(
            owner,
            "clients",
            {"name": self._text(name), "tax_id": normalized_tax, "trade_name": self._text(trade_name, required=False)},
            entity_id,
        )

    def save_account(
        self,
        owner: str,
        *,
        client_id: str,
        name: str,
        bank_code: str = "",
        branch: str = "",
        account_number: str = "",
        entity_id: str | None = None,
    ) -> dict:
        return self._save(
            owner,
            "accounts",
            {
                "client_id": client_id,
                "name": self._text(name),
                "bank_code": self._text(bank_code, limit=20, required=False),
                "branch": self._text(branch, limit=30, required=False),
                "account_number": self._text(account_number, limit=80, required=False),
            },
            entity_id,
        )

    def save_period(self, owner: str, *, name: str, start_date: date, end_date: date, entity_id: str | None = None) -> dict:
        if end_date < start_date:
            raise ValueError("A data final deve ser igual ou posterior à data inicial.")
        return self._save(
            owner, "periods", {"name": self._text(name), "start_date": start_date.isoformat(), "end_date": end_date.isoformat()}, entity_id
        )

    def contexts(self, owner: str, job_ids: list[str]) -> dict[str, dict]:
        if not job_ids:
            return {}
        with self.db.connect() as conn:
            organization = self._organization(conn, owner)
            if organization is None:
                return {}
            placeholders = ", ".join("?" for _ in job_ids)
            rows = self.db.fetchall(
                conn,
                f"SELECT * FROM pro_conversion_contexts WHERE organization_id = ? AND job_id IN ({placeholders})",
                (organization, *job_ids),
            )
            result = {
                row["job_id"]: {"client_id": row["client_id"], "account_id": row["account_id"], "version": row["version"], "period_ids": []}
                for row in rows
            }
            periods = self.db.fetchall(
                conn,
                "SELECT job_id, period_id FROM pro_conversion_periods "
                f"WHERE organization_id = ? AND job_id IN ({placeholders}) ORDER BY period_id",
                (organization, *job_ids),
            )
            for row in periods:
                result[row["job_id"]]["period_ids"].append(row["period_id"])
            return result

    def save_context(
        self, owner: str, *, job_id: str, client_id: str | None, account_id: str | None, period_ids: list[str], expected_version: int
    ) -> dict:
        with self.db.connect() as conn:
            result = self.save_context_in_connection(conn, owner, job_id=job_id, client_id=client_id, account_id=account_id,
                                                     period_ids=period_ids, expected_version=expected_version)
            conn.commit()
        return result

    def save_context_in_connection(
        self, conn, owner: str, *, job_id: str, client_id: str | None, account_id: str | None, period_ids: list[str], expected_version: int
    ) -> dict:
        periods = sorted(set(period_ids))
        if len(periods) > 50:
            raise ValueError("Selecione no máximo 50 períodos por conversão.")
        now = self._now()
        organization = self._organization(conn, owner, create=True)
        if client_id:
            self._entity(conn, "pro_clients", organization, client_id)
        if account_id:
            account = self._entity(conn, "pro_accounts", organization, account_id)
            if account["client_id"] != client_id:
                raise ValueError("A conta selecionada deve pertencer ao cliente da conversão.")
        for period in periods:
            self._entity(conn, "pro_periods", organization, period)
        if expected_version == 0:
            changed = self._write(
                conn,
                """INSERT INTO pro_conversion_contexts
                (organization_id, job_id, client_id, account_id, version, created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?) ON CONFLICT (organization_id, job_id) DO NOTHING""",
                (organization, job_id, client_id, account_id, owner, owner, now, now),
            )
        else:
            changed = self._write(
                conn,
                """UPDATE pro_conversion_contexts SET client_id = ?, account_id = ?,
                version = version + 1, updated_by = ?, updated_at = ? WHERE organization_id = ? AND job_id = ? AND version = ?""",
                (client_id, account_id, owner, now, organization, job_id, expected_version),
            )
        if changed != 1:
            raise OrganizationConflict("A organização deste arquivo mudou. Atualize a lista antes de salvar novamente.")
        self._write(conn, "DELETE FROM pro_conversion_periods WHERE organization_id = ? AND job_id = ?", (organization, job_id))
        for period in periods:
            self._write(conn, "INSERT INTO pro_conversion_periods (organization_id, job_id, period_id) VALUES (?, ?, ?)",
                        (organization, job_id, period))
        return {"client_id": client_id, "account_id": account_id, "period_ids": periods, "version": expected_version + 1}
