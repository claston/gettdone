import argparse
import json
import sys

from app.application import AccessControlService, InvalidUserTokenError
from app.dependencies import get_access_control_service


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Grant or revoke admin role for an existing user by email."
    )
    parser.add_argument(
        "--email",
        required=True,
        help="Target user email (example: admin@ofxsimples.com.br).",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="Revoke admin role instead of granting it.",
    )
    return parser


def main(argv: list[str] | None = None, service: AccessControlService | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    access_control = service or get_access_control_service()
    target_email = str(args.email or "").strip().lower()
    if not target_email:
        print("ERROR: --email is required.", file=sys.stderr)
        return 2

    target_is_admin = not bool(args.revoke)
    try:
        user = access_control.get_user_by_email(target_email)
    except InvalidUserTokenError:
        print(f"ERROR: User not found for email '{target_email}'.", file=sys.stderr)
        return 1

    updated = access_control.set_user_admin_role(
        user_id=user.user_id,
        is_admin=target_is_admin,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "user_id": updated["user_id"],
                "email": updated["email"],
                "is_admin": bool(updated["is_admin"]),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
