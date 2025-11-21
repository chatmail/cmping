import string
import argparse
import logging
import sys
import secrets
import time

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


ALPHANUMERIC = string.ascii_lowercase + string.digits
ALPHANUMERIC_PUNCT = string.ascii_letters + string.digits + string.punctuation

def newmail():
    user = "".join(random.choices(ALPHANUMERIC, 9)
    password = "".join(
        secrets.choice(ALPHANUMERIC_PUNCT)
        for _ in range(25)
    )
    return dict(email=f"{user}@{config.mail_domain}", password=f"{password}")


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        dc = DeltaChat(rpc)
        system_info = dc.get_system_info()
        print(f"Running deltachat core {system_info.deltachat_core_version}", file=sys.stderr)

        ac1 = dc.add_account()
        ac2 = dc.add_account()

        ac1.set_config("mdns_enabled", "0")
        ac2.set_config("mdns_enabled", "0")
        ac1.set_config("addr", f"ci_{ac1.get_config('addr')[3:]}")
        ac2.set_config("addr", f"ci_{ac2.get_config('addr')[3:]}")

        ac1.bring_online()
        ac2.bring_online()

        chat1 = ac1.create_chat(ac2)
        chat2 = ac2.create_chat(ac1)

        addr1 = ac1.get_config("configured_addr")
        addr2 = ac2.get_config("configured_addr")
        print(f"PING {relay1} ({addr1}) -> {relay2} ({addr2}))")
        for i in range(60):
            text = f"ping {i:59}"
            start = time.time()
            chat1.send_text(text)
            assert ac2.wait_for_incoming_msg().get_snapshot().text == text
            print(f"{len(text)} bytes from {relay2} to {relay2} seq={i} time={time.time() - start}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
