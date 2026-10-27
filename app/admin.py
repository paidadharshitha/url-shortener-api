"""Local operator commands for safely assigning links created before accounts existed."""

import argparse

from app.auth import AuthService
from app.config import Settings
from app.repository import LinkRepository


def claim_legacy(repository: LinkRepository, username: str) -> int:
    """Assign only unowned links, preserving existing account ownership and history."""
    with repository.connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user = connection.execute(
            "SELECT id FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        if user is None:
            raise ValueError("No account has that username. Create the account first.")
        result = connection.execute(
            "UPDATE links SET owner_id = ? WHERE owner_id IS NULL", (user["id"],)
        )
        return result.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    claim = subparsers.add_parser("claim-legacy", help="Assign unowned legacy links to an account.")
    claim.add_argument("username", help="An existing account's username.")
    args = parser.parse_args()
    settings = Settings.from_env()
    repository = LinkRepository(settings.database_path)
    repository.initialize()
    AuthService(repository, settings=settings).initialize()
    try:
        count = claim_legacy(repository, args.username)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Assigned {count} previously unowned link(s) to {args.username.strip().lower()}.")


if __name__ == "__main__":
    main()
