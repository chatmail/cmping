"""
chatmail ping aka "cmping" transmits messages between relays.
"""

import argparse
import ipaddress
import os
import queue
import random
import signal
import string
import sys
import threading
import time
import urllib.parse
from statistics import stdev

from deltachat_rpc_client import DeltaChat, EventType, Rpc
from xdg_base_dirs import xdg_cache_home


def is_ip_address(host):
    """Check if the given host is an IP address."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def generate_credentials():
    """Generate random username and password for IP-based login.

    Returns:
        tuple: (username, password) where username is 12 chars and password is 20 chars
    """
    chars = string.ascii_lowercase + string.digits
    username = "".join(random.choices(chars, k=12))
    password = "".join(random.choices(chars, k=20))
    return username, password


def create_qr_url(domain_or_ip):
    """Create either a dcaccount or dclogin URL based on input type.

    Args:
        domain_or_ip: Either a domain name or an IP address

    Returns:
        str: Either dcaccount:domain or dclogin:username@ip/?p=password&v=1&ip=993&sp=465&ic=3&ss=default
    """
    if is_ip_address(domain_or_ip):
        # Generate credentials for IP address
        username, password = generate_credentials()

        # Build dclogin URL according to spec
        # dclogin:username@ip/?p=password&v=1&ip=993&sp=465&ic=3&ss=default
        encoded_password = urllib.parse.quote(password, safe="")

        # Format: dclogin:username@host/?query
        qr_url = (
            f"dclogin:{username}@{domain_or_ip}/?"
            f"p={encoded_password}&v=1&ip=993&sp=465&ic=3&ss=default"
        )
        return qr_url
    else:
        # Use dcaccount for domain names
        return f"dcaccount:{domain_or_ip}"


def main():
    """Ping between addresses of specified chatmail relay domains or IP addresses."""

    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument(
        "relay1",
        action="store",
        help="chatmail relay domain or IP address",
    )
    parser.add_argument(
        "relay2",
        action="store",
        nargs="?",
        help="chatmail relay domain or IP address (defaults to relay1 if not specified)",
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
        "-g",
        dest="numrecipients",
        type=int,
        default=1,
        help="number of group recipients (default 1)",
    )
    args = parser.parse_args()
    if not args.relay2:
        args.relay2 = args.relay1

    pinger = perform_ping(args)
    expected_total = pinger.sent * args.numrecipients
    raise SystemExit(0 if pinger.received == expected_total else 1)


class AccountMaker:
    def __init__(self, dc, verbose=0):
        self.dc = dc
        self.online = []
        self.verbose = verbose

    def wait_all_online(self):
        remaining = list(self.online)
        while remaining:
            ac = remaining.pop()
            while True:
                event = ac.wait_for_event()
                if event.kind == EventType.IMAP_INBOX_IDLE:
                    break
                elif event.kind == EventType.ERROR and self.verbose >= 1:
                    print(f"✗ ERROR during account setup: {event.msg}")

    def _add_online(self, account):
        account.start_io()
        self.online.append(account)

    def get_relay_account(self, domain):
        # Try to find an existing account for this domain/IP
        for account in self.dc.get_all_accounts():
            addr = account.get_config("configured_addr")
            if addr is not None:
                # Extract the domain/IP from the configured address
                addr_domain = addr.split("@")[1] if "@" in addr else None
                if addr_domain == domain:
                    if account not in self.online:
                        break
        else:
            account = self.dc.add_account()
            qr_url = create_qr_url(domain)
            try:
                account.set_config_from_qr(qr_url)
            except Exception as e:
                print(f"✗ Failed to configure account on {domain}: {e}")
                raise

        try:
            self._add_online(account)
        except Exception as e:
            print(f"✗ Failed to bring account online for {domain}: {e}")
            raise

        return account


def setup_accounts(args, maker):
    """Set up sender and receiver accounts with progress display.

    Returns:
        tuple: (sender_account, list_of_receiver_accounts)
    """
    # Calculate total accounts needed
    total_accounts = 1 + args.numrecipients
    accounts_created = 0

    # Create sender account with progress
    print(
        f"# Setting up accounts: {accounts_created}/{total_accounts}",
        end="",
        flush=True,
    )
    try:
        sender = maker.get_relay_account(args.relay1)
        accounts_created += 1
        print(
            f"\r# Setting up accounts: {accounts_created}/{total_accounts}",
            end="",
            flush=True,
        )
    except Exception as e:
        print(f"\r✗ Failed to setup sender account on {args.relay1}: {e}")
        sys.exit(1)

    # Create receiver accounts with progress
    receivers = []
    for i in range(args.numrecipients):
        try:
            receiver = maker.get_relay_account(args.relay2)
            receivers.append(receiver)
            accounts_created += 1
            print(
                f"\r# Setting up accounts: {accounts_created}/{total_accounts}",
                end="",
                flush=True,
            )
        except Exception as e:
            print(f"\r✗ Failed to setup receiver account {i+1} on {args.relay2}: {e}")
            sys.exit(1)

    # Account setup complete
    print(f"\r# Setting up accounts: {accounts_created}/{total_accounts} - Complete!")

    return sender, receivers


def create_and_promote_group(sender, receivers):
    """Create a group chat and send initial message to promote it.

    Returns:
        group: The created group chat object
    """
    # Create a group chat from sender and add all receivers
    group = sender.create_group("cmping")
    for receiver in receivers:
        # Create a contact for the receiver account and add to group
        contact = sender.create_contact(receiver)
        group.add_contact(contact)

    # Send an initial message to promote the group
    # This sends invitations to all members
    print("# promoting group chat by sending initial message")
    group.send_text("cmping group chat initialized")

    return group


def wait_for_receivers_to_join(args, sender, receivers, timeout_seconds=30):
    """Wait concurrently for all receivers to join the group with progress display.

    Args:
        args: Command line arguments (for verbose flag)
        sender: Sender account
        receivers: List of receiver accounts
        timeout_seconds: Maximum time to wait for all receivers

    Returns:
        int: Number of receivers that successfully joined
    """
    print("# waiting for receivers to join group", end="", flush=True)
    sender_addr = sender.get_config("addr")
    start_time = time.time()

    # Track which receivers have joined
    joined_receivers = set()
    receiver_threads_queue = queue.Queue()

    def wait_for_receiver_join(idx, receiver, deadline):
        """Thread function to wait for a single receiver to join.

        Args:
            idx: Index of the receiver
            receiver: Receiver account object
            deadline: Timestamp when timeout should occur

        Note:
            Communicates results via receiver_threads_queue, does not return values.
            Queue messages: ("joined", idx, addr), ("error", idx, msg),
                          ("timeout", idx, None), ("exception", idx, error_str)
        """
        try:
            while time.time() < deadline:
                event = receiver.wait_for_event()
                if event.kind == EventType.INCOMING_MSG:
                    msg = receiver.get_message_by_id(event.msg_id)
                    snapshot = msg.get_snapshot()
                    sender_contact = msg.get_sender_contact()
                    sender_contact_snapshot = sender_contact.get_snapshot()
                    if (
                        sender_contact_snapshot.address == sender_addr
                        and "cmping group chat initialized" in snapshot.text
                    ):
                        chat_id = snapshot.chat_id
                        receiver_group = receiver.get_chat_by_id(chat_id)
                        receiver_group.accept()
                        receiver_threads_queue.put(
                            ("joined", idx, receiver.get_config("addr"))
                        )
                        return
                elif event.kind == EventType.ERROR and args.verbose >= 1:
                    receiver_threads_queue.put(("error", idx, event.msg))
            # Timeout occurred
            receiver_threads_queue.put(("timeout", idx, None))
        except Exception as e:
            receiver_threads_queue.put(("exception", idx, str(e)))

    # Start a thread for each receiver
    deadline = start_time + timeout_seconds
    threads = []
    for idx, receiver in enumerate(receivers):
        t = threading.Thread(
            target=wait_for_receiver_join, args=(idx, receiver, deadline)
        )
        t.start()
        threads.append(t)

    # Monitor progress and show spinner
    total_receivers = len(receivers)
    while len(joined_receivers) < total_receivers and time.time() < deadline:
        try:
            event_type, idx, data = receiver_threads_queue.get(timeout=0.5)
            if event_type == "joined":
                joined_receivers.add(idx)
                print(
                    f"\r# waiting for receivers to join group {len(joined_receivers)}/{total_receivers}",
                    end="",
                    flush=True,
                )
            elif event_type == "error":
                print(f"\n✗ ERROR during group joining for receiver {idx}: {data}")
                print(
                    f"# waiting for receivers to join group {len(joined_receivers)}/{total_receivers}",
                    end="",
                    flush=True,
                )
            elif event_type == "timeout":
                print(
                    f"\n# WARNING: receiver {idx} did not join group within {timeout_seconds}s"
                )
                print(
                    f"# waiting for receivers to join group {len(joined_receivers)}/{total_receivers}",
                    end="",
                    flush=True,
                )
            elif event_type == "exception":
                print(f"\n# ERROR: receiver {idx} encountered exception: {data}")
                print(
                    f"# waiting for receivers to join group {len(joined_receivers)}/{total_receivers}",
                    end="",
                    flush=True,
                )
        except queue.Empty:
            # Update spinner even when no events
            print(
                f"\r# waiting for receivers to join group {len(joined_receivers)}/{total_receivers}",
                end="",
                flush=True,
            )

    # Wait for threads to complete with a short timeout
    for t in threads:
        t.join(timeout=1.0)

    # Final status
    print(
        f"\r# waiting for receivers to join group {len(joined_receivers)}/{total_receivers} - Complete!"
    )

    # Check if all receivers joined
    if len(joined_receivers) < total_receivers:
        print(
            f"# WARNING: Only {len(joined_receivers)}/{total_receivers} receivers joined the group"
        )

    return len(joined_receivers)


def perform_ping(args):
    accounts_dir = xdg_cache_home().joinpath("cmping")
    print(f"# using accounts_dir at: {accounts_dir}")
    with Rpc(accounts_dir=accounts_dir) as rpc:
        dc = DeltaChat(rpc)
        maker = AccountMaker(dc, verbose=args.verbose)

        # Set up sender and receiver accounts
        sender, receivers = setup_accounts(args, maker)

        # Wait for all accounts to be online with timeout feedback
        print("# Waiting for all accounts to be online...", end="", flush=True)
        try:
            maker.wait_all_online()
            print(" Done!")
        except Exception as e:
            print(f"\n✗ Timeout or error waiting for accounts to be online: {e}")
            sys.exit(1)

        # Create group and promote it
        group = create_and_promote_group(sender, receivers)

        # Wait for all receivers to join the group
        wait_for_receivers_to_join(args, sender, receivers)

        pinger = Pinger(args, sender, group, receivers)
        received = {}
        # Track current sequence for output formatting
        current_seq = None
        # Track timing for each sequence: {seq: {'count': N, 'first_time': ms, 'last_time': ms, 'size': bytes}}
        seq_tracking = {}
        try:
            for seq, ms_duration, size, receiver_idx in pinger.receive():
                if seq not in received:
                    received[seq] = []
                received[seq].append(ms_duration)

                # Track timing for this sequence
                if seq not in seq_tracking:
                    seq_tracking[seq] = {
                        "count": 0,
                        "first_time": ms_duration,
                        "last_time": ms_duration,
                        "size": size,
                    }
                seq_tracking[seq]["count"] += 1
                seq_tracking[seq]["last_time"] = ms_duration

                # Print new line for new sequence or first message
                if current_seq != seq:
                    if current_seq is not None:
                        print()  # End previous line
                    # Start new line for this sequence
                    print(
                        f"{size} bytes ME -> {pinger.relay1} -> {pinger.relay2} -> ME seq={seq} time={ms_duration:0.2f}ms",
                        end="",
                        flush=True,
                    )
                    current_seq = seq

                # Print N/M ratio with in-place update (spinning effect)
                count = seq_tracking[seq]["count"]
                total = args.numrecipients
                # Calculate how many characters we need to overwrite from previous ratio
                if count > 1:
                    # Backspace over previous ratio to update in-place
                    prev_count = count - 1
                    prev_ratio_len = len(f" {prev_count}/{total}")
                    print("\b" * prev_ratio_len, end="", flush=True)
                print(f" {count}/{total}", end="", flush=True)

                # If all receivers have received, print elapsed time
                if count == total:
                    first_time = seq_tracking[seq]["first_time"]
                    last_time = seq_tracking[seq]["last_time"]
                    elapsed = last_time - first_time
                    print(f" (elapsed: {elapsed:0.2f}ms)", end="", flush=True)

        except KeyboardInterrupt:
            pass
        if current_seq is not None:
            print()  # End last line
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
            t = threading.Thread(
                target=receiver_thread, args=(idx, receiver), daemon=True
            )
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
                elif event.kind == EventType.ERROR and self.args.verbose >= 1:
                    print(f"✗ ERROR: {event.msg}")
                elif event.kind == EventType.MSG_FAILED and self.args.verbose >= 1:
                    msg = receiver.get_message_by_id(event.msg_id)
                    text = msg.get_snapshot().text
                    print(f"✗ Message failed: {text}")
                elif (
                    event.kind in (EventType.INFO, EventType.WARNING)
                    and self.args.verbose >= 1
                ):
                    ms_now = (time.time() - start_clock) * 1000
                    print(f"INFO {ms_now:07.1f}ms: {event.msg}")
            except queue.Empty:
                # Timeout occurred, check if we should continue
                continue


if __name__ == "__main__":
    main()
