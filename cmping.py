"""
chatmail ping aka "cmping" transmits messages between relays.

Message Flow:
=============
1. ACCOUNT SETUP: Create sender and receiver accounts on specified relay domains
   - Each account connects to its relay's IMAP/SMTP servers
   - Accounts wait for IMAP_INBOX_IDLE state indicating readiness

2. GROUP CREATION: Sender creates a group chat and adds all receivers

3. PING SEND: Sender transmits messages to the group at specified intervals
   - Messages contain: unique-id timestamp sequence-number
   - Messages flow: Sender -> relay1 SMTP -> relay2 IMAP -> Receivers

4. PING RECEIVE: Each receiver waits for incoming messages
   - On receipt, round-trip time is calculated from embedded timestamp
   - Progress is tracked per-sequence across all receivers
   - Stats are accumulated for final report
"""

import argparse
import contextlib
import ipaddress
import logging
import queue
import random
import shutil
import string
import sys
import threading
import time
import urllib.parse
from statistics import stdev

from deltachat_rpc_client import AttrDict, DeltaChat, EventType, Rpc
from xdg_base_dirs import xdg_cache_home

log = logging.getLogger("cmping")

# Controls CLI output (progress spinners, per-message RTT lines, statistics).
# Library callers can set this to False to suppress all terminal output while
# keeping structured log messages (phase=online, phase=setup, etc.) visible.
_cli_output = True


def set_cli_output(enabled):
    """Enable or disable CLI output (progress spinners, statistics)."""
    global _cli_output
    _cli_output = enabled


class CMPingError(Exception):
    """Raised when cmping encounters a non-recoverable error during probing."""
    pass


def classify_failure(raw):
    """Classify a raw error message into a failure category.

    Ported from gocmping's classifyFailure

    Returns one of: syntax, auth, dns, timeout, policy_reject, unknown.
    """
    msg = raw.lower()
    if any(s in msg for s in ("bad recipient address syntax", "recipient address syntax", "5.1.3", "syntax")):
        return "syntax"
    if any(s in msg for s in ("auth", "authentication", "5.7.8", "535")):
        return "auth"
    if any(s in msg for s in ("no such host", "nxdomain", "dns")):
        return "dns"
    if any(s in msg for s in ("timed out", "timeout", "deadline exceeded")):
        return "timeout"
    if any(s in msg for s in ("policy", "blocked", "deny", "rejected", "reject", "5.7.1")):
        return "policy_reject"
    return "unknown"


class FailureTracker:
    """Thread-safe tracker for failure events, classified by phase and category.

    Ported from gocmping's failureTracker
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._counts = {}    # {phase:category: count}
        self._examples = {}  # {phase:category: first_raw_example}

    def add(self, phase, raw):
        """Record a failure event.

        Args:
            phase: The phase where the failure occurred (e.g. "join", "send").
            raw: The raw error message string.
        """
        category = classify_failure(raw)
        key = f"{phase}:{category}"
        with self._lock:
            self._counts[key] = self._counts.get(key, 0) + 1
            if key not in self._examples and raw:
                self._examples[key] = raw

    def snapshot(self):
        """Return a sorted list of failure summaries.

        Returns:
            list[dict]: Each dict has keys: phase, category, count, example.
        """
        with self._lock:
            out = []
            for key, count in self._counts.items():
                parts = key.split(":", 1)
                phase = parts[0] if len(parts) == 2 else key
                category = parts[1] if len(parts) == 2 else "unknown"
                out.append({
                    "phase": phase,
                    "category": category,
                    "count": count,
                    "example": self._examples.get(key, ""),
                })
            out.sort(key=lambda x: (x["phase"], x["category"]))
            return out


# Spinner characters for progress display
SPINNER_CHARS = ["|", "/", "-", "\\"]


class RelayContext:
    """Context for a relay including its RPC connection, DeltaChat instance, and account maker.

    Can be used as a context manager for automatic cleanup, or managed
    manually via open()/close() for long-lived relay pools.
    """

    def __init__(self, relay, accounts_dir, verbose=0):
        """Prepare a RelayContext (does not start RPC yet).

        Args:
            relay: The relay domain or IP address.
            accounts_dir: Path to the accounts directory for this relay.
            verbose: Verbosity level passed to AccountMaker.
        """
        from pathlib import Path
        self.relay = relay
        self.accounts_dir = Path(accounts_dir)
        self.verbose = verbose
        self.rpc = None
        self.dc = None
        self.maker = None

    def open(self):
        """Start the RPC server and initialize DeltaChat + AccountMaker.

        Returns self for chaining.
        """
        if self.accounts_dir.exists() and not self.accounts_dir.joinpath("accounts.toml").exists():
            shutil.rmtree(self.accounts_dir)
        self.rpc = Rpc(accounts_dir=self.accounts_dir)
        self.rpc.__enter__()
        self.dc = DeltaChat(self.rpc)
        self.maker = AccountMaker(self.dc, verbose=self.verbose)
        return self

    def close(self):
        """Shut down the RPC server."""
        if self.rpc is not None:
            try:
                self.rpc.__exit__(None, None, None)
            except Exception as e:
                log.warning("cleanup failed for %s: %s", self.relay, e)
            self.rpc = None
            self.dc = None
            self.maker = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()
        return False


def log_event_verbose(event, addr, verbose_level=3):
    """Helper function to log events at specified verbose level."""
    if hasattr(event, "msg") and event.msg:
        log.debug(f"[{addr}] {event.kind}: {event.msg}")
    else:
        log.debug(f"[{addr}] {event.kind}")


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


def print_progress(message, current=None, total=None, spinner_idx=0, done=False):
    """Print progress with optional spinner and counter.

    Suppressed when _cli_output is False (library mode) or when the cmping
    logger is above INFO level.

    Args:
        message: The progress message to display
        current: Current count (optional)
        total: Total count (optional)
        spinner_idx: Index into SPINNER_CHARS for spinner animation
        done: If True, print 'Done!' and newline
    """
    if not _cli_output:
        return
    if done:
        sys.stderr.write(f"\r# {message}... Done!".ljust(60) + "\n")
        sys.stderr.flush()
    elif current is not None and total is not None:
        spinner = SPINNER_CHARS[spinner_idx % len(SPINNER_CHARS)]
        sys.stderr.write(f"\r# {message} {spinner} {current}/{total}")
        sys.stderr.flush()
    else:
        spinner = SPINNER_CHARS[spinner_idx % len(SPINNER_CHARS)]
        sys.stderr.write(f"\r# {message} {spinner}")
        sys.stderr.flush()


def format_duration(seconds):
    """Format a duration in seconds to a human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        str: Formatted duration (e.g., "1.23s" or "45.67ms")
    """
    if seconds >= 1:
        return f"{seconds:.2f}s"
    else:
        return f"{seconds * 1000:.2f}ms"


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
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove all account directories of tested relays to force fresh account creation",
    )
    args = parser.parse_args()
    if not args.relay2:
        args.relay2 = args.relay1

    # Configure logging based on verbose level.
    if args.verbose >= 3:
        level = logging.DEBUG
    elif args.verbose >= 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        pinger = perform_ping(args)
    except CMPingError as e:
        log.error(f"{e}")
        raise SystemExit(1)
    expected_total = pinger.sent * args.numrecipients
    raise SystemExit(0 if pinger.received == expected_total else 1)


class AccountMaker:
    def __init__(self, dc, verbose=0):
        self.dc = dc
        self.online = []
        self.verbose = verbose

    def _log_event(self, event, addr):
        """Helper method to log events at verbose level 3."""
        if self.verbose >= 3:
            if hasattr(event, "msg") and event.msg:
                log.debug(f"{event.kind}: {event.msg} [{addr}]")
            else:
                log.debug(f"{event.kind} [{addr}]")

    def wait_all_online(self, timeout=None):
        deadline = time.time() + timeout if timeout is not None else None
        remaining = list(self.online)
        while remaining:
            ac = remaining.pop()
            eq = ac._rpc.get_queue(ac.id)
            while True:
                if deadline is not None and time.time() >= deadline:
                    addr = ac.get_config("addr")
                    raise CMPingError(f"Timeout waiting for {addr} to come online")
                try:
                    event = AttrDict(eq.get(timeout=1.0))
                except queue.Empty:
                    continue
                if event.kind == EventType.IMAP_INBOX_IDLE:
                    if self.verbose >= 3:
                        addr = ac.get_config("addr")
                        log.debug(f"IMAP_INBOX_IDLE: {addr} is now idle and ready")
                    break
                elif event.kind == EventType.ERROR and self.verbose >= 1:
                    log.warning(f"ERROR during profile setup: {event.msg}")
                elif self.verbose >= 3:
                    # Show all events during online phase when verbose level 3
                    addr = ac.get_config("addr")
                    self._log_event(event, addr)

    def _add_online(self, account):
        if self.verbose >= 3:
            addr = account.get_config("addr")
            log.debug(f"Starting I/O for account: {addr}")

        # Enable bot mode in all accounts before starting I/O
        # so we don't have to accept contact requests.
        account.set_config("bot", "1")
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
                        if self.verbose >= 3:
                            log.debug(f"Reusing existing account: {addr}")
                        break
        else:
            account = self.dc.add_account()
            if self.verbose >= 3:
                log.debug(f"Creating new account for domain: {domain}")
            qr_url = create_qr_url(domain)
            try:
                if self.verbose >= 3:
                    log.debug(f"Configuring account from QR: {domain}")
                account.set_config_from_qr(qr_url)
                if self.verbose >= 3:
                    addr = account.get_config("addr")
                    log.debug(f"Account configured: {addr}")
            except Exception as e:
                log.error(f"Failed to configure profile on {domain}: {e}")
                raise

        try:
            self._add_online(account)
        except Exception as e:
            log.error(f"Failed to bring profile online for {domain}: {e}")
            raise

        return account


def setup_accounts(args, sender_maker, receiver_maker):
    """Set up sender and receiver accounts with progress display.

    Timing: This function's duration is tracked as 'account_setup_time'.

    Args:
        args: Command line arguments
        sender_maker: AccountMaker for the sender's relay
        receiver_maker: AccountMaker for the receiver's relay

    Returns:
        tuple: (sender_account, list_of_receiver_accounts)
    """
    # Calculate total profiles needed
    total_profiles = 1 + args.numrecipients
    profiles_created = 0

    # Create sender and receiver accounts with spinner
    print_progress("Setting up profiles", profiles_created, total_profiles, 0)

    try:
        sender = sender_maker.get_relay_account(args.relay1)
        profiles_created += 1
        print_progress("Setting up profiles", profiles_created, total_profiles, profiles_created)
    except Exception as e:
        raise CMPingError(f"Failed to setup sender profile on {args.relay1}: {e}") from e

    # Create receiver accounts
    receivers = []
    for i in range(args.numrecipients):
        try:
            receiver = receiver_maker.get_relay_account(args.relay2)
            receivers.append(receiver)
            profiles_created += 1
            print_progress("Setting up profiles", profiles_created, total_profiles, profiles_created)
        except Exception as e:
            raise CMPingError(
                f"Failed to setup receiver profile {i+1} on {args.relay2}: {e}"
            ) from e

    # Profile setup complete
    print_progress("Setting up profiles", done=True)

    return sender, receivers


def create_group(sender, receivers, verbose=0):
    """Create a group chat.

    Returns:
        group: The created group chat object
    """
    # Create a group chat from sender and add all receivers
    if verbose >= 3:
        log.debug("Creating group chat 'cmping'")
    group = sender.create_group("cmping")
    for receiver in receivers:
        # Create a contact for the receiver account and add to group
        contact = sender.create_contact(receiver)
        if verbose >= 3:
            receiver_addr = receiver.get_config("addr")
            log.debug(f"Adding {receiver_addr} to group")
        group.add_contact(contact)

    return group


def wait_profiles_online(maker, timeout=None):
    """Wait for all profiles to be online with spinner progress.

    Args:
        maker: AccountMaker instance with accounts to wait for
        timeout: Optional seconds before giving up and raising CMPingError

    Raises:
        CMPingError: If waiting for profiles fails
    """
    # Flag to indicate when wait_all_online is complete
    online_complete = threading.Event()
    online_error = None

    def wait_online_thread():
        nonlocal online_error
        try:
            maker.wait_all_online(timeout=timeout)
        except Exception as e:
            online_error = e
        finally:
            online_complete.set()

    # Start the wait in a separate thread
    wait_thread = threading.Thread(target=wait_online_thread)
    wait_thread.start()

    # Show spinner while waiting
    spinner_idx = 0
    while not online_complete.is_set():
        print_progress("Waiting for profiles to be online", spinner_idx=spinner_idx)
        spinner_idx += 1
        online_complete.wait(timeout=0.1)

    wait_thread.join()

    if online_error:
        raise CMPingError(
            f"Timeout or error waiting for profiles to be online: {online_error}"
        ) from online_error

    print_progress("Waiting for profiles to be online", done=True)


def wait_profiles_online_multi(makers, timeout=None):
    """Wait for all profiles to be online with spinner progress.

    Args:
        makers: List of AccountMaker instances with accounts to wait for
        timeout: Optional seconds before giving up and raising CMPingError

    Raises:
        CMPingError: If waiting for profiles fails
    """
    online_errors = []

    def wait_online_thread(maker):
        try:
            maker.wait_all_online(timeout=timeout)
        except Exception as e:
            online_errors.append(e)

    # Start a thread for each maker
    threads = []
    for maker in makers:
        wait_thread = threading.Thread(target=wait_online_thread, args=(maker,))
        wait_thread.start()
        threads.append(wait_thread)

    # Show spinner while waiting
    spinner_idx = 0
    while any(t.is_alive() for t in threads):
        print_progress("Waiting for profiles to be online", spinner_idx=spinner_idx)
        spinner_idx += 1
        time.sleep(0.1)

    for t in threads:
        t.join()

    if online_errors:
        raise CMPingError(
            f"Timeout or error waiting for profiles to be online: {online_errors[0]}"
        ) from online_errors[0]

    print_progress("Waiting for profiles to be online", done=True)


def perform_ping(args, accounts_dir=None, timeout=None):
    """Main ping execution function with timing measurements.

    Creates per-relay RelayContext instances, delegates to
    perform_ping_with_contexts(), and cleans up on exit.  Behavior is
    identical to previous versions for all existing callers.

    Args:
        args: Namespace with relay1, relay2, count, interval, verbose,
              numrecipients, reset attributes.
        accounts_dir: Optional base directory for account storage.
              Defaults to $XDG_CACHE_HOME/cmping. Override this to isolate
              concurrent probes (each needs its own DB to avoid locking).
        timeout: Optional per-phase timeout in seconds.

    Timing Phases:
    1. account_setup_time: Time to create and configure all accounts
    2. message_time: Time to send and receive all ping messages

    Returns:
        Pinger: The pinger object with results.
            Also has account_setup_time, message_time (float, seconds)
            and results list of (seq, ms_duration, receiver_idx) tuples.

    Raises:
        CMPingError: On account setup or connectivity failures.
    """
    if accounts_dir is not None:
        from pathlib import Path
        base_accounts_dir = Path(accounts_dir)
    else:
        base_accounts_dir = xdg_cache_home().joinpath("cmping")

    # Validate relay names before using them as path components.

    # Determine unique relays being tested. Using a set to deduplicate when
    # relay1 == relay2 (same relay testing), so we only create one RPC context.
    relays = list({args.relay1, args.relay2})

    # Handle --reset option: remove account directories for tested relays
    if args.reset:
        for relay in relays:
            relay_dir = base_accounts_dir.joinpath(relay)
            if relay_dir.exists():
                log.info(f"Removing account directory for {relay}: {relay_dir}")
                shutil.rmtree(relay_dir)

    # Create and open per-relay contexts.
    relay_contexts = {}
    try:
        for relay in relays:
            relay_dir = base_accounts_dir.joinpath(relay)
            log.info(f"using accounts_dir for {relay} at: {relay_dir}")
            ctx = RelayContext(relay, relay_dir, verbose=args.verbose)
            try:
                ctx.open()
            except Exception as e:
                log.error(f"Failed to initialize RPC for {relay}: {e}")
                raise
            relay_contexts[relay] = ctx

        return perform_ping_with_contexts(args, relay_contexts, timeout=timeout)
    finally:
        for ctx in relay_contexts.values():
            ctx.close()


def perform_ping_with_contexts(args, relay_contexts, timeout=None):
    """Core ping logic using pre-opened RelayContext instances.

    This is the public API for callers that manage their own relay
    lifecycle (e.g. relay pools that keep contexts alive across rounds).

    Args:
        args: Namespace with relay1, relay2, count, interval, verbose,
              numrecipients attributes (reset not used here).
        relay_contexts: dict mapping relay name -> open RelayContext.
        timeout: Optional per-phase timeout in seconds.

    Returns:
        Pinger: The pinger object with results.
            Has account_setup_time, message_time (float, seconds),
            results list of (seq, ms_duration, receiver_idx) tuples, and
            failures (FailureTracker).

    Raises:
        CMPingError: On account setup or connectivity failures.
    """
    failures = FailureTracker()
    relays = list({args.relay1, args.relay2})

    # Phase 1: Account Setup (timed)
    account_setup_start = time.time()

    # Set up sender and receiver accounts using per-relay makers
    sender_maker = relay_contexts[args.relay1].maker
    receiver_maker = relay_contexts[args.relay2].maker
    sender, receivers = setup_accounts(args, sender_maker, receiver_maker)

    # Wait for all accounts to be online with timeout feedback
    all_makers = [relay_contexts[r].maker for r in relays]
    wait_profiles_online_multi(all_makers, timeout=timeout)

    account_setup_time = time.time() - account_setup_start

    group = create_group(sender, receivers, verbose=args.verbose)

    # Phase 2: Message Ping/Pong (timed)
    message_start = time.time()

    pinger = Pinger(args, sender, group, receivers)
    if timeout is not None:
        pinger.deadline = time.time() + timeout
    received = {}
    # Track current sequence for output formatting
    current_seq = None
    # Track timing for each sequence
    seq_tracking = {}
    # Gate CLI output on _cli_output flag -- silent when used as library.
    show_output = _cli_output
    try:
        for seq, ms_duration, size, receiver_idx in pinger.receive():
            if seq not in received:
                received[seq] = []
            received[seq].append(ms_duration)
            pinger.results.append((seq, ms_duration, receiver_idx))

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

            if not show_output:
                continue

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

    pinger._send_thread.join(timeout=2.0)
    message_time = time.time() - message_start

    if show_output:
        if current_seq is not None:
            print()  # End last line

        # Print statistics - show full addresses only in verbose >= 2
        if args.verbose >= 2:
            receivers_info = pinger.receivers_addrs_str
        else:
            receivers_info = f"{len(pinger.receivers_addrs)} receivers"
        print(f"--- {pinger.addr1} -> {receivers_info} statistics ---")
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

    # Print timing and rate statistics
    print("--- timing statistics ---")
    print(f"account setup: {format_duration(account_setup_time)}")
    print(f"message send/recv: {format_duration(message_time)}")

    # Calculate message rates
    if message_time > 0 and pinger.sent > 0:
        send_rate = pinger.sent / message_time
        print(f"send rate: {send_rate:.2f} msg/s")
    if message_time > 0 and pinger.received > 0:
        recv_rate = pinger.received / message_time
        print(f"recv rate: {recv_rate:.2f} msg/s")

    # Store timing and failure data on pinger
    pinger.account_setup_time = account_setup_time
    pinger.message_time = message_time
    pinger.failures = failures

    return pinger


class Pinger:
    """Handles sending ping messages and receiving responses.

    Message Flow:
    1. send_pings() runs in a background thread, sending messages at intervals
    2. Each message contains: unique_id timestamp sequence_number
    3. Messages are sent to a group chat (single send, multiple receivers)
    4. receive() yields (seq, duration, size, receiver_idx) for each received message
    5. Multiple receivers may receive each sequence number

    Attributes:
        sent: Number of messages sent
        received: Number of messages received (across all receivers)
        loss: Percentage of expected messages not received
    """

    def __init__(self, args, sender, group, receivers):
        """Initialize Pinger and start sending messages.

        Args:
            args: Command line arguments
            sender: Sender account object
            group: Group chat object
            receivers: List of receiver account objects
        """
        self.args = args
        self.sender = sender
        self.group = group
        self.receivers = receivers
        self.addr1 = sender.get_config("addr")
        self.receivers_addrs = [receiver.get_config("addr") for receiver in receivers]
        self.receivers_addrs_str = ", ".join(self.receivers_addrs)
        self.relay1 = self.addr1.split("@")[1]
        self.relay2 = self.receivers_addrs[0].split("@")[1]

        log.info(
            "CMPING %s(%s) -> %s(group with %d receivers) count=%d interval=%ss",
            self.relay1, self.addr1, self.relay2, len(receivers),
            args.count, args.interval,
        )
        ALPHANUMERIC = string.ascii_lowercase + string.digits
        self.tx = "".join(random.choices(ALPHANUMERIC, k=30))
        self.sent = 0
        self.received = 0
        self.results = []  # list of (seq, ms_duration, receiver_idx)
        self.account_setup_time = 0.0
        self.message_time = 0.0
        # Optional wall-clock deadline for the messaging phase. When set,
        # send_pings() stops sending and receive() stops waiting at this time.
        # Set externally (e.g. by perform_ping) after setup phases complete.
        self.deadline = None
        # Signaled by send_pings() when it finishes so receive() can compute
        # a default deadline without the old os.kill(SIGINT) hack.
        self._stop_event = threading.Event()
        self._send_thread = threading.Thread(target=self.send_pings, daemon=True)
        self._send_thread.start()

    @property
    def loss(self):
        expected_total = self.sent * len(self.receivers)
        return 0.0 if expected_total == 0 else (1 - self.received / expected_total) * 100

    def send_pings(self):
        """Send ping messages to the group at regular intervals.

        Each message contains: unique_id timestamp sequence_number
        Flow: Sender -> SMTP relay1 -> IMAP relay2 -> All receivers

        Respects self.deadline: stops sending early when the wall clock passes
        the deadline so we don't fire pings we'll never wait for.

        When all pings are sent, signals _stop_event so receive() can set a
        grace-period deadline instead of blocking indefinitely.
        """
        for seq in range(self.args.count):
            if self.deadline is not None and time.time() >= self.deadline:
                break
            text = f"{self.tx} {time.time():.4f} {seq:17}"
            self.group.send_text(text)
            self.sent += 1
            time.sleep(self.args.interval)
        self._stop_event.set()

    def receive(self):
        """Receive ping messages from all receivers.

        Yields:
            tuple: (seq, ms_duration, size, receiver_idx) for each received message
                - seq: Sequence number of the message
                - ms_duration: Round-trip time in milliseconds
                - size: Size of the message in bytes
                - receiver_idx: Index of the receiver that received the message
        """
        num_pending = self.args.count * len(self.receivers)
        start_clock = time.time()
        # Track which sequence numbers have been received by which receiver
        received_by_receiver = {}

        # Create a queue to collect events from all receivers
        event_queue = queue.Queue()

        stop_event = threading.Event()

        def receiver_thread(receiver_idx, receiver):
            """Thread function to listen to events from a single receiver."""
            # Use a timeout-based poll so the thread exits promptly when
            # stop_event is set, rather than blocking indefinitely on
            # queue.get() inside wait_for_event() and leaking across rounds.
            account_queue = receiver._rpc.get_queue(receiver.id)
            while not stop_event.is_set():
                try:
                    item = account_queue.get(timeout=1.0)
                    event_queue.put((receiver_idx, receiver, AttrDict(item)))
                except queue.Empty:
                    continue
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

        try:
            while num_pending > 0:
                # When send_pings finishes and no explicit deadline was set,
                # compute a default grace period so receive() doesn't block
                # indefinitely.  Replaces the old os.kill(SIGINT) hack.
                if self.deadline is None and self._stop_event.is_set():
                    self.deadline = time.time() + 60.0
                if self.deadline is not None and time.time() >= self.deadline:
                    break
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
                        elif self.args.verbose >= 3:
                            # Log non-ping messages at verbose level 3
                            receiver_addr = self.receivers_addrs[receiver_idx]
                            ellipsis = "..." if len(text) > 50 else ""
                            log.debug(f"[{receiver_addr}] INCOMING_MSG (non-ping): {text[:50]}{ellipsis}")
                    elif event.kind == EventType.ERROR and self.args.verbose >= 1:
                        log.warning(f"ERROR: {event.msg}")
                    elif event.kind == EventType.MSG_FAILED and self.args.verbose >= 1:
                        msg = receiver.get_message_by_id(event.msg_id)
                        text = msg.get_snapshot().text
                        log.warning(f"Message failed: {text}")
                    elif (
                        event.kind in (EventType.INFO, EventType.WARNING)
                        and self.args.verbose >= 1
                    ):
                        ms_now = (time.time() - start_clock) * 1000
                        log.info(f"{ms_now:.1f}ms: {event.msg}")
                    elif self.args.verbose >= 3:
                        # Log all other events at verbose level 3
                        receiver_addr = self.receivers_addrs[receiver_idx]
                        log_event_verbose(event, receiver_addr)
                except queue.Empty:
                    # Timeout occurred, check if we should continue
                    continue
        finally:
            stop_event.set()
            for t in threads:
                t.join(timeout=2.0)


if __name__ == "__main__":
    main()
