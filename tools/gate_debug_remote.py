#!/usr/bin/env python3
"""Remote interactive debugger for FTL2 gate processes.

Uploads a gate .pyz to a remote host via SFTP, starts it over SSH,
and provides the same interactive prompt as gate_debug.py.

Usage:
    python tools/gate_debug_remote.py <gate.pyz> <host> [options]

Options:
    -s, --subsystem Connect via SSH subsystem (no gate upload needed)
    -u, --user      SSH username (default: current user)
    -p, --port      SSH port (default: 22)
    -i, --identity  SSH private key file
    -I, --interpreter  Remote Python interpreter (default: /usr/bin/python3)

Examples:
    python tools/gate_debug_remote.py ~/.ftl/ftl_gate_abc.pyz myhost
    python tools/gate_debug_remote.py ~/.ftl/ftl_gate_abc.pyz myhost -u admin -p 2222
    python tools/gate_debug_remote.py -s myhost -u root
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path


def encode_message(msg_type: str, data: dict) -> bytes:
    """Encode a message using the gate protocol (8-byte hex length + JSON)."""
    body = json.dumps([msg_type, data])
    length = len(body.encode("utf-8"))
    return f"{length:08x}{body}".encode("utf-8")


def format_response(msg_type: str, data: dict) -> str:
    """Format a gate response for display."""
    if msg_type == "InfoResult":
        lines = [f"<< {msg_type}:"]
        for key, val in data.items():
            lines.append(f"   {key:20s} {val}")
        return "\n".join(lines)
    elif msg_type == "ListModulesResult":
        modules = data.get("modules", [])
        lines = [f"<< {msg_type}: {len(modules)} module(s)"]
        for m in modules:
            lines.append(f"   {m['name']:30s} {m['type']}")
        return "\n".join(lines)
    else:
        return f"<< {msg_type}: {json.dumps(data)}"


async def read_response(reader) -> tuple[str, dict] | None:
    """Read a single length-prefixed JSON response."""
    length_bytes = await reader.read(8)
    if not length_bytes or len(length_bytes) < 8:
        return None
    length = int(length_bytes[:8].decode("ascii"), 16)
    body = await reader.read(length)
    if not body or len(body) < length:
        return None
    msg_type, data = json.loads(body.decode("utf-8"))
    return msg_type, data


async def run(args: argparse.Namespace) -> None:
    import asyncssh

    if not args.subsystem:
        if not args.gate:
            print("Gate file path required (or use -s for subsystem)")
            sys.exit(1)
        gate_path = Path(args.gate).expanduser().resolve()
        if not gate_path.exists():
            print(f"Gate file not found: {gate_path}")
            sys.exit(1)

    # Build SSH connection options
    connect_kwargs: dict = {
        "host": args.host,
        "port": args.port,
        "username": args.user,
        "known_hosts": None,
    }
    if args.identity:
        connect_kwargs["client_keys"] = [args.identity]

    print(f"Connecting to {args.user}@{args.host}:{args.port}...")

    async with asyncssh.connect(**connect_kwargs) as conn:
        if args.subsystem:
            # Connect via SSH subsystem — no upload needed
            print("Connecting via SSH subsystem 'ftl2-gate'...")
            try:
                process = await conn.create_process(
                    subsystem="ftl2-gate", encoding=None
                )
            except asyncssh.ChannelOpenError:
                print("Subsystem 'ftl2-gate' not registered on remote host.")
                print("Register it first with gate_subsystem=True, or run without -s.")
                sys.exit(1)
            print("Connected via subsystem.")
        else:
            # Upload gate via SFTP
            remote_gate = f"/tmp/{gate_path.name}"
            print(f"Uploading gate to {remote_gate}...")

            async with conn.start_sftp_client() as sftp:
                needs_upload = True
                if await sftp.exists(remote_gate):
                    remote_stat = await sftp.lstat(remote_gate)
                    local_size = gate_path.stat().st_size
                    if remote_stat.size == local_size:
                        print("Gate already exists on remote, reusing.")
                        needs_upload = False

                if needs_upload:
                    await sftp.put(str(gate_path), remote_gate)
                    await conn.run(f"chmod 700 {remote_gate}", check=True)
                    print("Upload complete.")

            # Start gate process
            interpreter = args.interpreter
            print(f"Starting gate: {interpreter} {remote_gate}")
            process = await conn.create_process(
                f"{interpreter} {remote_gate}", encoding=None
            )

        # Handshake
        process.stdin.write(encode_message("Hello", {}))
        response = await read_response(process.stdout)
        if response is None or response[0] != "Hello":
            stderr = await process.stderr.read()
            print(f"Gate handshake failed: {stderr.decode(errors='replace')}")
            sys.exit(1)

        print(f"{format_response(*response)}")
        print()
        print("Commands: hello, info, list, watch <path>, unwatch <path>, listen, shutdown, module <name> [args_json], raw <json>, quit")
        print()

        # Interactive loop
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    line = await loop.run_in_executor(None, lambda: input("gate> "))
                    line = line.strip()
                except EOFError:
                    break

                if not line:
                    continue

                parts = line.split(None, 1)
                cmd = parts[0].lower()

                if cmd == "hello":
                    msg = encode_message("Hello", {})

                elif cmd == "info":
                    msg = encode_message("Info", {})

                elif cmd == "list":
                    msg = encode_message("ListModules", {})

                elif cmd == "watch":
                    path = parts[1].strip() if len(parts) > 1 else ""
                    if not path:
                        print("Usage: watch <path>")
                        continue
                    msg = encode_message("Watch", {"path": path})

                elif cmd == "unwatch":
                    path = parts[1].strip() if len(parts) > 1 else ""
                    if not path:
                        print("Usage: unwatch <path>")
                        continue
                    msg = encode_message("Unwatch", {"path": path})

                elif cmd == "listen":
                    print("Listening for events (Ctrl+C to stop)...")
                    try:
                        while True:
                            response = await read_response(process.stdout)
                            if response:
                                print(format_response(*response))
                            else:
                                print("<< Connection closed")
                                break
                    except (KeyboardInterrupt, asyncio.CancelledError):
                        pass
                    print("Stopped listening.")
                    continue

                elif cmd == "shutdown":
                    process.stdin.write(encode_message("Shutdown", {}))
                    response = await read_response(process.stdout)
                    if response:
                        print(format_response(*response))
                    process.stdin.write_eof()
                    break

                elif cmd == "module":
                    mod_args = parts[1] if len(parts) > 1 else ""
                    mod_parts = mod_args.split(None, 1)
                    module_name = mod_parts[0] if mod_parts else "ping"
                    module_args = {}
                    if len(mod_parts) > 1:
                        try:
                            module_args = json.loads(mod_parts[1])
                        except json.JSONDecodeError:
                            print("Invalid JSON for module args")
                            continue
                    msg = encode_message(
                        "Module",
                        {"module_name": module_name, "module_args": module_args},
                    )

                elif cmd == "raw":
                    if len(parts) < 2:
                        print("Usage: raw <json>")
                        continue
                    try:
                        parsed = json.loads(parts[1])
                        if isinstance(parsed, list) and len(parsed) == 2:
                            msg = encode_message(parsed[0], parsed[1])
                        else:
                            print("Expected [msg_type, data]")
                            continue
                    except json.JSONDecodeError:
                        print("Invalid JSON")
                        continue

                elif cmd == "quit":
                    process.stdin.write_eof()
                    break

                else:
                    print(f"Unknown command: {cmd}")
                    print("Commands: hello, info, list, watch <path>, unwatch <path>, listen, shutdown, module <name> [args_json], raw <json>, quit")
                    continue

                process.stdin.write(msg)
                response = await read_response(process.stdout)
                if response:
                    print(format_response(*response))
                else:
                    print("<< No response (connection closed)")
                    break

        except KeyboardInterrupt:
            print()
            process.stdin.write_eof()


def main() -> None:
    import getpass

    parser = argparse.ArgumentParser(description="Remote FTL2 gate debugger")
    parser.add_argument("gate", nargs="?", help="Path to gate .pyz file (not needed with -s)")
    parser.add_argument("host", help="Remote host")
    parser.add_argument("-s", "--subsystem", action="store_true",
                        help="Connect via SSH subsystem instead of uploading gate")
    parser.add_argument("-u", "--user", default=getpass.getuser(), help="SSH username")
    parser.add_argument("-p", "--port", type=int, default=22, help="SSH port")
    parser.add_argument("-i", "--identity", help="SSH private key file")
    parser.add_argument(
        "-I", "--interpreter", default="/usr/bin/python3",
        help="Remote Python interpreter",
    )

    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
