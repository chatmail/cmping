import argparse
import logging
import sys

from deltachat_rpc_client import DeltaChat, EventType, Rpc, events, run_bot_cli


def main():
    parser = argparse.ArgumentParser(
        description="ping between addresses of chatmail relays"
    )

    parser.add_argument(
        "relay1",
        action="store",
        help="chatmail relay domain",
    )
    parser.add_argument(
        "relay2",
        action="store",
        help="chatmail relay domain",
    )
    args = parser.parse_args()

    perform_ping(args.relay1, args.relay2)


def log_verbose(msg, args):
    print(msg % args)


hooks = events.HookCollection()


@hooks.on(events.RawEvent)
def log_event(event):
    if event.kind == EventType.INFO:
        logging.info(event.msg)
    elif event.kind == EventType.WARNING:
        logging.warning(event.msg)


@hooks.on(events.RawEvent(EventType.ERROR))
def log_error(event):
    logging.error(event.msg)


@hooks.on(events.NewMessage())
def echo(event):
    snapshot = event.message_snapshot
    if snapshot.text:
        if not snapshot.text.startswith("> "):
            snapshot.chat.send_message(text=f"> {snapshot.text}")
        else:
            print(f"ECHO FROM {snapshot.sender}")


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        deltachat = DeltaChat(rpc)
        system_info = deltachat.get_system_info()
        log_verbose("Running deltachat core %s", system_info.deltachat_core_version)
        log_verbose("systeminfo %s", system_info)

        accounts = deltachat.get_all_accounts()
        if len(accounts) == 0:
            sender = deltachat.add_account()
            sender.set_config_from_qr(f"dcaccount:https://{relay1}/new")
            receiver = deltachat.add_account()
            receiver.set_config_from_qr(f"dcaccount:https://{relay2}/new")
            accounts = [sender, receiver]
        assert len(accounts) == 2, "remove accounts folder"
        for client in accounts:
            if not client.is_configured():
                configure_thread.
                configure_thread.start()
        sender, receiver = accounts

        i = 0
        while True:
            sender.send_text(str(0))
            time.sleep(1)
            i += 1



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
