"""cockpit-admin CLI for user management."""
import argparse
import getpass
import sys


def cmd_user_add(args) -> int:
    pw = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm: ")
    if pw != pw2:
        print("Passwords do not match", file=sys.stderr)
        return 1
    # TODO: bcrypt hash, write to SQLite
    print(f"[STUB] would add user {args.username} role={args.role}")
    return 0


def cmd_user_list(args) -> int:
    # TODO
    print("[STUB] user-list not implemented")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="cockpit-admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("user-add")
    add.add_argument("--username", required=True)
    add.add_argument("--role", choices=["admin", "user"], default="user")
    add.set_defaults(func=cmd_user_add)

    sub.add_parser("user-list").set_defaults(func=cmd_user_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
