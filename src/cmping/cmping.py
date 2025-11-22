import argparse
import random
import string
import sys
import threading
import time
from statistics import stdev
from xdg_base_dirs import xdg_cache_home

from deltachat_rpc_client import DeltaChat, EventType, Rpc


def main():
    """Ping between addresses of specified chatmail relay domains. """

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "relay1",
        action="store",
        help="chatmail relay domain",
    )
    parser.add_argument(
        "relay2",
        action="store",
        nargs="?",
        help="chatmail relay domain (defaults to relay1 if not specified)",
    )
    parser.add_argument(
        "-c",
        dest="count",
        type=int,
        default=30,
        help="number of pings",
    )
    args = parser.parse_args()
    if not args.relay2:
        args.relay2 = args.relay1

    perform_ping(args.count, args.relay1, args.relay2)


class AccountMaker:
    def __init__(self, dc):
        self.dc = dc
        self.online = []

    def wait_all_online(self):
        remaining = list(self.online)
        while remaining:
            ac = remaining.pop()
            ac.wait_for_event(EventType.IMAP_INBOX_IDLE)

    def _add_online(self, account):
        account.start_io()
        self.online.append(account)

    def get_relay_account(self, domain):
        for account in self.dc.get_all_accounts():
            addr = account.get_config("configured_addr")
            if addr is not None and addr.split("@")[1] == domain:
                if account not in self.online:
                    break
        else:
            print(f"# creating account on {domain}")
            account = self.dc.add_account()
            account.set_config_from_qr(f"dcaccount:{domain}")

        self._add_online(account)
        return account


def perform_ping(count, relay1, relay2):
    accounts_dir = xdg_cache_home().joinpath("cmping")
    print(f"# using accounts_dir at: {accounts_dir}")
    with Rpc(accounts_dir=accounts_dir) as rpc:
        dc = DeltaChat(rpc)
        maker = AccountMaker(dc)
        sender = maker.get_relay_account(relay1)
        receiver = maker.get_relay_account(relay2)
        maker.wait_all_online()
        _ = receiver.create_chat(sender)

        pinger = Pinger(count, sender, receiver)
        received = {}
        try:
            for seq, ms_duration, size in pinger.receive():
                print(
                    f"{size} bytes ME -> {pinger.addr1} -> {pinger.addr2} -> ME seq={seq} time={ms_duration:0.2f}ms"
                )
                received[seq] = ms_duration

        except KeyboardInterrupt:
            pass
        print(f"--- {pinger.addr1} -> {pinger.addr2} statistics ---")
        print(
            f"{pinger.sent} transmitted, {pinger.received} received, {pinger.loss:.2f}% loss"
        )
        if received:
            rmin = min(received.values())
            ravg = sum(received.values()) / len(received)
            rmax = max(received.values())
            rmdev = stdev(received.values()) if len(received) >= 2 else rmax
            print(
                f"rtt min/avg/max/mdev = {rmin:.3f}/{ravg:.3f}/{rmax:.3f}/{rmdev:.3f} ms"
            )


class Pinger:
    def __init__(self, count, sender, receiver):
        self.count = count
        self.sender = sender
        self.receiver = receiver
        self.addr1, self.addr2 = sender.get_config("addr"), receiver.get_config("addr")
        relay1 = self.addr1.split("@")[1]
        relay2 = self.addr2.split("@")[1]

        print(f"PING {relay1}({self.addr1}) -> {relay2}({self.addr2}) count={count}")
        ALPHANUMERIC = string.ascii_lowercase + string.digits
        self.tx = "".join(random.choices(ALPHANUMERIC, k=30))
        t = threading.Thread(target=self.send_pings)
        t.setDaemon(True)
        self.sent = 0
        self.received = 0
        t.start()

    @property
    def loss(self):
        return 1 if self.sent == 0 else (1 - self.received / self.sent) * 100

    def send_pings(self):
        chat1 = self.sender.create_chat(self.receiver)
        for seq in range(self.count):
            text = f"{self.tx} {time.time():.4f} {seq:17}"
            chat1.send_text(text)
            self.sent += 1
            time.sleep(1.1)

    def receive(self):
        num_pending = self.count
        while num_pending > 0:
            event = self.receiver.wait_for_event()
            if event.kind == EventType.INCOMING_MSG:
                msg = self.receiver.get_message_by_id(event.msg_id)
                text = msg.get_snapshot().text
                parts = text.strip().split()
                if len(parts) == 3 and parts[0] == self.tx:
                    ms_duration = (time.time() - float(parts[1])) * 1000
                    self.received += 1
                    num_pending -= 1
                    yield int(parts[2]), ms_duration, len(text)
                # else:
                #    print(f"!received historic/bogus message from {self.addr2}: {text}")
            elif event.kind == EventType.ERROR:
                print(f"ERROR: {event.msg}")


if __name__ == "__main__":
    main()
