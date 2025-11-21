import argparse
import logging
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


def perform_ping(relay1, relay2):
    with Rpc() as rpc:
        dc = DeltaChat(rpc)
        system_info = dc.get_system_info()
        print(
            f"Running deltachat core {system_info.deltachat_core_version}",
            file=sys.stderr,
        )

        if len(dc.get_all_accounts()) != 2:
            assert len(dc.get_all_accounts()) == 0, "remove accounts directory"
            print(f"recreating accounts on {relay1} and {relay2}")
            ac1 = dc.add_account()
            ac2 = dc.add_account()
            ac1.set_config("mdns_enabled", "0")
            ac2.set_config("mdns_enabled", "0")
            ac1.set_config_from_qr(f"dcaccount:{relay1}")
            ac2.set_config_from_qr(f"dcaccount:{relay2}")
        else:
            ac1, ac2 = dc.get_all_accounts()

        ac1.bring_online()
        ac2.bring_online()

        chat1 = ac1.create_chat(ac2)
        _chat2 = ac2.create_chat(ac1)

        addr1, addr2 = ac1.get_config("addr"), ac2.get_config("addr")
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
