import argparse
import logging
import sys

from deltachat_rpc_client import DeltaChat, EventType, Rpc, events


def main(args=None):
    if args is None:
        args = list(sys.argv)

    parser = argparse.ArgumentParser(
        description="ping between addresses of chatmail relays"
    )

    parser.add_argument(
        dest="relay1",
        action="store",
        help="chatmail relay domain",
    )
    parser.add_argument(
        dest="relay2",
        action="store",
        help="chatmail relay domain",
    )
    args = parser.parse_args(args=args)

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


@hooks.on(events.NewMessage(func=lambda e: not e.command))
def echo(event):
    snapshot = event.message_snapshot
    if snapshot.text or snapshot.file:
        snapshot.chat.send_message(text=snapshot.text, file=snapshot.file)


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        deltachat = DeltaChat(rpc)
        system_info = deltachat.get_system_info()
        log_verbose("Running deltachat core %s", system_info.deltachat_core_version)

        accounts = deltachat.get_all_accounts()
        if len(accounts) == 0:
            sender = deltachat.add_account()
            sender.set_config_from_qr(f"dcaccount:https://{relay1}/new")
            receiver = deltachat.add_account()
            receiver.set_config_from_qr(f"dcaccount:https://{relay2}/new")
            assert 0

        assert len(accounts) == 2, len(accounts)

        account1, account2 = deltachat.get_all_accounts()


if __name__ == "__main__":
    main()
