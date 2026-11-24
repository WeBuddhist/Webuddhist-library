# ruff: noqa: T201
"""
CLI tool for managing API keys.

Usage:
    python scripts/api_key.py create --name "My Key" --email "user@example.com"
    python scripts/api_key.py create --name "App Key" --email "user@example.com" --application "app_id"
    python scripts/api_key.py list
    python scripts/api_key.py revoke --id "key_id"
    python scripts/api_key.py rotate --id "key_id"

Requires NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD environment variables.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import settings
from database import Database
from database.api_key_database import ApiKeyDatabase
from identifier import generate_id


def _run[T](operation: Callable[[ApiKeyDatabase], Awaitable[T]]) -> T:
    async def run() -> T:
        db = Database.from_settings(settings)
        try:
            return await operation(db.api_key)
        finally:
            await db.close()

    return asyncio.run(run())


def create_api_key(name: str, email: str, application_id: str | None = None) -> None:
    """Create a new API key, optionally bound to an application."""
    key_id = generate_id()

    try:
        created_id, raw_key = _run(lambda keys: keys.create(key_id, name, email, application_id))
    except ValueError as e:
        print(f"\n❌ {e}\n")
        sys.exit(1)

    print("\n✅ API key created successfully!")
    print(f"   ID:          {created_id}")
    print(f"   Name:        {name}")
    print(f"   Email:       {email}")
    print(f"   Bound to:    {application_id or '(none - master key)'}")
    print(f"   API Key:     {raw_key}")
    print("\n⚠️  IMPORTANT: Save this API key now. It will not be shown again!\n")


def list_api_keys() -> None:
    """List all API keys."""

    keys = _run(lambda keys: keys.list_all())

    if not keys:
        print("\nNo API keys found.\n")
        return

    print(f"\n{'ID':<20} {'Name':<20} {'Email':<30} {'Active':<8} {'Bound To':<20} {'Created':<20}")
    print("-" * 120)

    for key in keys:
        created = str(key.get("created_at", "N/A"))[:19] if key.get("created_at") else "N/A"
        active = "Yes" if key.get("is_active") else "No"
        bound_to = key.get("bound_application_id") or "(master)"
        email = key.get("email", "N/A")

        print(f"{key['id']:<20} {key['name']:<20} {email:<30} {active:<8} {bound_to:<20} {created:<20}")

    print()


def revoke_api_key(key_id: str) -> None:
    """Revoke an API key."""

    if _run(lambda keys: keys.revoke(key_id)):
        print(f"\n✅ API key '{key_id}' has been revoked.\n")
    else:
        print(f"\n❌ API key '{key_id}' not found.\n")
        sys.exit(1)


def rotate_api_key(key_id: str) -> None:
    """Generate a new value for an API key."""

    new_key = _run(lambda keys: keys.rotate_key(key_id))

    if new_key:
        print("\n✅ API key rotated successfully!")
        print(f"   ID:          {key_id}")
        print(f"   New API Key: {new_key}")
        print("\n⚠️  IMPORTANT: Save this API key now. It will not be shown again!")
        print("   The old key is now invalid.\n")
    else:
        print(f"\n❌ API key '{key_id}' not found.\n")
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Manage API keys",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s create --name "Master Admin Key" --email "admin@example.com"
  %(prog)s create --name "WeBuddhist Key" --email "dev@example.com" --application webuddhist
  %(prog)s list
  %(prog)s revoke --id abc123
  %(prog)s rotate --id abc123
        """,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create a new API key")
    create_parser.add_argument("--name", required=True, help="Name for the API key")
    create_parser.add_argument("--email", required=True, help="Contact email for the key owner")
    create_parser.add_argument(
        "--application",
        required=False,
        help="Application ID to bind the key to (omit for master key)",
    )

    subparsers.add_parser("list", help="List all API keys")

    revoke_parser = subparsers.add_parser("revoke", help="Revoke an API key")
    revoke_parser.add_argument("--id", required=True, help="API key ID to revoke")

    rotate_parser = subparsers.add_parser("rotate", help="Generate a new value for an API key")
    rotate_parser.add_argument("--id", required=True, help="API key ID to rotate")

    args = parser.parse_args()

    if args.command == "create":
        create_api_key(args.name, args.email, args.application)
    elif args.command == "list":
        list_api_keys()
    elif args.command == "revoke":
        revoke_api_key(args.id)
    elif args.command == "rotate":
        rotate_api_key(args.id)


if __name__ == "__main__":
    main()
