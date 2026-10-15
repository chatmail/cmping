import pytest
from deltachat_rpc_client import DeltaChat, EventType, Rpc

from cmping import AccountMaker


@pytest.fixture
def maker(tmp_path):
    with Rpc(accounts_dir=tmp_path) as rpc:
        yield AccountMaker(DeltaChat(rpc))


def test_wait_all_online_times_out(maker):
    account = maker.dc.add_account()
    maker.online.append(account)
    with pytest.raises(TimeoutError, match="not online after 0.5s"):
        maker.wait_all_online(timeout=0.5)


def test_wait_all_online_returns_on_idle(maker):
    account = maker.dc.add_account()
    maker.online.append(account)
    maker.dc.rpc.get_queue(account.id).put({"kind": EventType.IMAP_INBOX_IDLE})
    maker.wait_all_online(timeout=5)
