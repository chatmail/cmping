import argparse
import logging
import random
import secrets
import string
import sys
import time

from deltachat_rpc_client import DeltaChat, Rpc


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


def newmail(domain):
    user = "ci_" + "".join(random.choices(ALPHANUMERIC, k=6))
    password = "".join(secrets.choice(ALPHANUMERIC_PUNCT) for _ in range(25))
    return f"{user}@{domain}", f"{password}"


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        dc = DeltaChat(rpc)
        system_info = dc.get_system_info()
        print(
            f"Running deltachat core {system_info.deltachat_core_version}",
            file=sys.stderr,
        )

        ac1 = dc.add_account()
        ac2 = dc.add_account()
        ac1.set_config("mdns_enabled", "0")
        ac2.set_config("mdns_enabled", "0")
        addr1, mail_pw = newmail(relay1)
        ac1.set_config("addr", addr1)
        ac1.set_config("mail_pw", mail_pw)
        ac1.configure()
        addr2, mail_pw = newmail(relay2)
        ac2.set_config("addr", addr2)
        ac2.set_config("mail_pw", mail_pw)
        ac2.configure()

        ac1.bring_online()
        ac2.bring_online()

        chat1 = ac1.create_chat(ac2)
        _chat2 = ac2.create_chat(ac1)

        print(f"PING {relay1} ({addr1}) -> {relay2} ({addr2}))")
        for i in range(60):
            text = f"ping {i:59}"
            start = time.time()
            chat1.send_text(text)
            msg = ac2.wait_for_incoming_msg().get_snapshot()
            assert msg.text == text
            print(
                f"{len(text)} bytes [ME] -> {relay1} -> {relay2} -> [ME] seq={i} time={time.time() - start:.4}s"
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
