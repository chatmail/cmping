"""
chatmail ping aka "cmping" transmits messages between relays.
"""

import argparse
import os
import queue
import random
import signal
import string
import threading
import time
from statistics import stdev

from deltachat_rpc_client import DeltaChat, EventType, Rpc
from xdg_base_dirs import xdg_cache_home


def main():
    """Ping between addresses of specified chatmail relay domains."""

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
        help="number of message pings",
    )
    parser.add_argument(
        "-i",
        dest="interval",
        type=float,
        default=1.1,
        help="seconds between message sending (default 1.1)",
    )
    parser.add_argument(
        "-v", dest="verbose", action="count", default=0, help="increase verbosity"
    )
    parser.add_argument(
        "-r",
        dest="numrecipients",
        type=int,
        default=1,
        help="number of recipients (default 1)",
    )
    args = parser.parse_args()
    if not args.relay2:
        args.relay2 = args.relay1

    pinger = perform_ping(args)
    expected_total = pinger.sent * args.numrecipients
    raise SystemExit(0 if pinger.received == expected_total else 1)


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


def perform_ping(args):
    accounts_dir = xdg_cache_home().joinpath("cmping")
    print(f"# using accounts_dir at: {accounts_dir}")
    with Rpc(accounts_dir=accounts_dir) as rpc:
        dc = DeltaChat(rpc)
        maker = AccountMaker(dc)
        sender = maker.get_relay_account(args.relay1)
        receivers = [maker.get_relay_account(args.relay2) for _ in range(args.numrecipients)]
        maker.wait_all_online()
        
        # Create a group chat from sender and add all receivers
        group = sender.create_group("cmping")
        for receiver in receivers:
            # Create a contact for the receiver account and add to group
            contact = sender.create_contact(receiver)
            group.add_contact(contact)
        
        # Note: The group is in "unpromoted" state until first message is sent.
        # When we send the first ping, it will promote the group and send invitations.
        # Receivers will automatically see and accept messages in the group.

        pinger = Pinger(args, sender, group, receivers)
        received = {}
        try:
            for seq, ms_duration, size, receiver_idx in pinger.receive():
                print(
                    f"{size} bytes ME -> {pinger.relay1} -> {pinger.relay2} -> ME seq={seq} receiver={receiver_idx} time={ms_duration:0.2f}ms"
                )
                if seq not in received:
                    received[seq] = []
                received[seq].append(ms_duration)

        except KeyboardInterrupt:
            pass
        print(f"--- {pinger.addr1} -> {pinger.receivers_addrs_str} statistics ---")
        print(
            f"{pinger.sent} transmitted, {pinger.received} received, {pinger.loss:.2f}% loss"
        )
        if received:
            all_durations = [d for durations in received.values() for d in durations]
            rmin = min(all_durations)
            ravg = sum(all_durations) / len(all_durations)
            rmax = max(all_durations)
            rmdev = stdev(all_durations) if len(all_durations) >= 2 else rmax
            print(
                f"rtt min/avg/max/mdev = {rmin:.3f}/{ravg:.3f}/{rmax:.3f}/{rmdev:.3f} ms"
            )
        return pinger


class Pinger:
    def __init__(self, args, sender, group, receivers):
        self.args = args
        self.sender = sender
        self.group = group
        self.receivers = receivers
        self.addr1 = sender.get_config("addr")
        self.receivers_addrs = [receiver.get_config("addr") for receiver in receivers]
        self.receivers_addrs_str = ", ".join(self.receivers_addrs)
        self.relay1 = self.addr1.split("@")[1]
        self.relay2 = self.receivers_addrs[0].split("@")[1]

        print(
            f"CMPING {self.relay1}({self.addr1}) -> {self.relay2}(group with {len(receivers)} members: {self.receivers_addrs_str}) count={args.count} interval={args.interval}s"
        )
        ALPHANUMERIC = string.ascii_lowercase + string.digits
        self.tx = "".join(random.choices(ALPHANUMERIC, k=30))
        t = threading.Thread(target=self.send_pings, daemon=True)
        self.sent = 0
        self.received = 0
        t.start()

    @property
    def loss(self):
        expected_total = self.sent * len(self.receivers)
        return 1 if expected_total == 0 else (1 - self.received / expected_total) * 100

    def send_pings(self):
        # Send to the group chat (single message to all recipients)
        for seq in range(self.args.count):
            text = f"{self.tx} {time.time():.4f} {seq:17}"
            self.group.send_text(text)
            self.sent += 1
            time.sleep(self.args.interval)
        # we sent all pings, let's wait a bit, then force quit if main didn't finish
        time.sleep(60)
        os.kill(os.getpid(), signal.SIGINT)

    def receive(self):
        num_pending = self.args.count * len(self.receivers)
        start_clock = time.time()
        # Track which sequence numbers have been received by which receiver
        received_by_receiver = {}
        
        # Create a queue to collect events from all receivers
        event_queue = queue.Queue()
        
        def receiver_thread(receiver_idx, receiver):
            """Thread function to listen to events from a single receiver"""
            while True:
                try:
                    event = receiver.wait_for_event()
                    event_queue.put((receiver_idx, receiver, event))
                except Exception:
                    # If there's an error, put it in the queue
                    event_queue.put((receiver_idx, receiver, None))
                    break
        
        # Start a thread for each receiver
        threads = []
        for idx, receiver in enumerate(self.receivers):
            t = threading.Thread(target=receiver_thread, args=(idx, receiver), daemon=True)
            t.start()
            threads.append(t)
        
        while num_pending > 0:
            try:
                receiver_idx, receiver, event = event_queue.get(timeout=1.0)
                if event is None:
                    continue
                    
                if event.kind == EventType.INCOMING_MSG:
                    msg = receiver.get_message_by_id(event.msg_id)
                    text = msg.get_snapshot().text
                    parts = text.strip().split()
                    if len(parts) == 3 and parts[0] == self.tx:
                        seq = int(parts[2])
                        if seq not in received_by_receiver:
                            received_by_receiver[seq] = set()
                        if receiver_idx not in received_by_receiver[seq]:
                            ms_duration = (time.time() - float(parts[1])) * 1000
                            self.received += 1
                            num_pending -= 1
                            received_by_receiver[seq].add(receiver_idx)
                            yield seq, ms_duration, len(text), receiver_idx
                            start_clock = time.time()
                elif event.kind == EventType.ERROR:
                    print(f"ERROR: {event.msg}")
                elif event.kind == EventType.MSG_FAILED:
                    msg = receiver.get_message_by_id(event.msg_id)
                    text = msg.get_snapshot().text
                    print(f"Message failed: {text}")
                elif event.kind in (EventType.INFO, EventType.WARNING) and self.args.verbose >= 1:
                    ms_now = (time.time() - start_clock) * 1000
                    print(f"INFO {ms_now:07.1f}ms: {event.msg}")
            except queue.Empty:
                # Timeout occurred, check if we should continue
                continue


if __name__ == "__main__":
    main()
