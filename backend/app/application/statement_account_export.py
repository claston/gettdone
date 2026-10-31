"""Preserve account scopes through reconciliation, editing and all export formats."""

import csv
import re
from collections import OrderedDict
from decimal import Decimal
from io import StringIO

from openpyxl import Workbook

from app.application.errors import InvalidFileContentError
from app.application.models import NormalizedTransaction
from app.application.ofx_identity import build_fit_id_sequence
from app.application.ofx_writer import build_ofx_statement
from app.application.reconciliation import ReconciliationResult


def reconcile_account_rows(transactions, scopes, reconcile):
    if len(scopes) != len(transactions) or any(not scope or not scope.get("account_id") for scope in scopes):
        raise InvalidFileContentError("Há lançamentos sem associação inequívoca à conta de origem.")
    groups = OrderedDict()
    for index, scope in enumerate(scopes):
        groups.setdefault(scope["account_id"], []).append(index)
    statuses, matched, reversed_count, duplicates = ["unmatched"] * len(transactions), 0, 0, 0
    for indices in groups.values():
        result = reconcile([transactions[i] for i in indices])
        for i, status in zip(indices, result.statuses, strict=True):
            statuses[i] = status
        matched += result.matched_groups
        reversed_count += result.reversed_entries
        duplicates += result.potential_duplicates
    return ReconciliationResult(statuses, matched, reversed_count, duplicates)


def statement_scopes(parts):
    return [dict(part["statement_account"]) for part in parts or [] if isinstance(part.get("statement_account"), dict)]


def account_groups(rows, scopes=None):
    groups = OrderedDict((scope["block_id"], (dict(scope), [])) for scope in scopes or [])
    for row in rows:
        scope = row.statement_account
        if not scope or any(not scope.get(key) for key in ("account_id", "block_id", "bank_code", "bank_branch", "account_number")):
            raise InvalidFileContentError("Um lançamento está sem sua conta de origem; a exportação foi interrompida.")
        key = scope["block_id"]
        if key in groups and not groups[key][1]:
            original = groups[key][0]
            if any(original.get(field) != scope.get(field) for field in ("account_id", "bank_code", "bank_branch", "account_number")):
                raise InvalidFileContentError("A conta do lançamento não corresponde ao bloco de origem.")
            groups[key] = (scope, [])
        if key in groups and groups[key][0] != scope:
            raise InvalidFileContentError("Há identidades ou controles conflitantes para o mesmo bloco de conta.")
        groups.setdefault(key, (scope, []))[1].append(row)
    return groups


def assert_account_overrides(rows, *, opening_balance=None, closing_balance=None, bank_branch=None, account_number=None, bank_code=None,
                             statement_accounts=None):
    scopes = [r.statement_account for r in rows if r.statement_account] or statement_accounts or []
    if scopes and (opening_balance is not None or closing_balance is not None or str(bank_branch or "").strip()
                   or str(account_number or "").strip()
                   or str(bank_code or "").strip() and any(str(s["bank_code"]) != str(bank_code).strip() for s in scopes)):
        raise InvalidFileContentError("Este PDF contém várias contas. Um saldo ou uma conta únicos não podem substituir suas origens.")


def account_ledger_balance(scope, rows):
    opening, closing = scope.get("opening_balance"), scope.get("closing_balance")
    if not scope.get("controls_validated") or opening is None or closing is None:
        return None
    active = [r for r in rows if not r.is_deleted]
    if any(not scope["period_start"] <= r.date <= scope["period_end"] for r in active):
        return None
    calculated = Decimal(str(opening)) + sum((Decimal(str(r.amount)) for r in active), Decimal(0))
    return closing if calculated == Decimal(str(closing)) else None


def refresh_edited_account_rows(preview_rows, report_rows, block_ids):
    for rows in (preview_rows, report_rows):
        running = {}
        for row in rows:
            scope = row.statement_account
            if not scope or scope["block_id"] not in block_ids:
                continue
            row.statement_account = {**scope, "controls_validated": False}
            row.warning_types = list(dict.fromkeys([*row.warning_types, "manual_review_recommended", "statement_account_edited"]))
            key = scope["block_id"]
            if key not in running:
                running[key] = Decimal(str(scope["opening_balance"])) if scope.get("opening_balance") is not None else None
            if not row.is_deleted and running[key] is not None:
                running[key] += Decimal(str(row.amount))
            row.running_balance = float(running[key]) if running[key] is not None else None


def _account_columns(row):
    scope = row.statement_account
    return [scope["bank_code"], scope["bank_branch"], scope["account_number"], scope["block_id"], row.source_page]


def write_account_workbook(path, rows, *, format_sheet, format_date, report=False, scopes=None):
    groups = account_groups(rows, scopes)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Transacoes" if report else "Conversao"
    sheet.append((["date", "description", "amount", "category", "reconciliation_status"] if report
                  else ["Data", "Histórico", "Crédito", "Débito", "Saldo"])
                 + ["Banco", "Agência", "Conta", "Bloco", "Página de origem"])
    for row in rows:
        if row.is_deleted:
            continue
        fields = ([row.date, row.description, row.amount, row.category, row.reconciliation_status] if report
                  else [format_date(row.date), row.description, row.amount if row.amount > 0 else None,
                        abs(row.amount) if row.amount < 0 else None, row.running_balance])
        sheet.append(fields + _account_columns(row))
    summary = workbook.create_sheet("Resumo")
    summary.append(["Banco", "Agência", "Conta", "Bloco", "Início", "Fim", "Saldo anterior", "Saldo final impresso", "Conferência"])
    for scope, block_rows in groups.values():
        summary.append([scope["bank_code"], scope["bank_branch"], scope["account_number"], scope["block_id"],
                        scope["period_start"], scope["period_end"], scope.get("opening_balance"), scope.get("closing_balance"),
                        "Conciliado por conta" if account_ledger_balance(scope, block_rows) is not None else "Revisão necessária"])
    for current in workbook:
        format_sheet(current)
    workbook.save(path)


def write_account_artifacts(output_dir, rows, *, format_sheet, format_date, scopes=None):
    groups = account_groups(rows, scopes)
    account_transactions = OrderedDict()
    for scope, block_rows in groups.values():
        account_transactions.setdefault(scope["account_id"], []).extend(
            NormalizedTransaction(r.date, r.description, r.amount, "inflow" if r.amount >= 0 else "outflow")
            for r in block_rows if not r.is_deleted)
    fit_ids = {key: iter(build_fit_id_sequence(transactions)) for key, transactions in account_transactions.items()}
    responses, prefix = [], None
    for scope, block_rows in groups.values():
        transactions = [NormalizedTransaction(r.date, r.description, r.amount, "inflow" if r.amount >= 0 else "outflow")
                        for r in block_rows if not r.is_deleted]
        statement = build_ofx_statement(transactions, bank_id=scope["bank_code"], bank_branch=scope["bank_branch"],
                                        account_number=scope["account_number"], closing_balance=account_ledger_balance(scope, block_rows),
                                        fit_ids=[next(fit_ids[scope["account_id"]]) for _ in transactions])
        if "<DTASOF>" in statement:
            statement = re.sub(r"<DTASOF>[^\n]+", "<DTASOF>" + scope["period_end"].replace("-", "") + "000000[-3:BRT]", statement)
        before, _, body = statement.partition("  <BANKMSGSRSV1>\n")
        response, _, _ = body.partition("  </BANKMSGSRSV1>")
        prefix = before if prefix is None else prefix
        responses.append(response)
    (output_dir / "converted.ofx").write_text(prefix + "  <BANKMSGSRSV1>\n" + "".join(responses)
                                               + "  </BANKMSGSRSV1>\n</OFX>\n", encoding="utf-8")
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["date", "description", "amount", "category", "reconciliation_status",
                     "bank_code", "bank_branch", "account_number", "statement_block", "source_page"])
    for row in rows:
        if not row.is_deleted:
            writer.writerow([row.date, row.description, row.amount, row.category, row.reconciliation_status] + _account_columns(row))
    (output_dir / "converted.csv").write_text(buffer.getvalue(), encoding="utf-8")
    write_account_workbook(output_dir / "converted.xlsx", rows, format_sheet=format_sheet, format_date=format_date, scopes=scopes)
