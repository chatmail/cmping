import argparse
import sys
import time

from deltachat_rpc_client import DeltaChat, EventType, Rpc


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


def get_relay_account(dc, domain):
    for account in dc.get_all_accounts():
        if account.get_config("addr").split("@")[1] == domain:
            account.bring_online()
            return account

    print(f"creating account on {domain}")
    account = dc.add_account()
    account.set_config("mdns_enabled", "0")
    account.set_config_from_qr(f"dcaccount:{domain}")
    account.bring_online()
    return account


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        dc = DeltaChat(rpc)
        system_info = dc.get_system_info()
        print(
            f"Running deltachat core {system_info.deltachat_core_version}",
            file=sys.stderr,
        )

        sender = get_relay_account(dc, relay1)
        receiver = get_relay_account(dc, relay2)
        chat1 = sender.create_chat(receiver)
        _chat2 = receiver.create_chat(sender)

        addr1, addr2 = sender.get_config("addr"), receiver.get_config("addr")
        print(f"PING {relay1} ({addr1}) -> {relay2} ({addr2}))")
        for i in range(60):
            text = f"ping {i:59}"
            start = time.time()
            chat1.send_text(text)
            while 1:
                event = receiver.wait_for_event()
                if event.kind == EventType.INCOMING_MSG:
                    msg = receiver.get_message_by_id(event.msg_id)
                    if msg.get_snapshot().text == text:
                        break
                    print(f"received historic/bogus message from {addr2}: {msg.text}")
                elif event.kind == EventType.ERROR:
                    print(f"ERROR: {event.msg}")
            print(
                f"{len(text)} bytes ME -> {relay1} -> {relay2} -> ME seq={i} time={time.time() - start:.2}s"
            )
            time.sleep(1)


if __name__ == "__main__":
    main()
