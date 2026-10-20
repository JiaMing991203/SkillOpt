"""research/classify_listeners.py: strict vLLM listener policy.

Fixture lines mirror job 64330104's `ss -ltnpH` output (node IP replaced).
"""
from research.classify_listeners import classify, rendezvous_port

HTTP = "LISTEN 0 128 127.0.0.1:48061 0.0.0.0:* users:((\"vllm\",pid=547099,fd=24))"
TCPSTORE = "LISTEN 0 128 0.0.0.0:36883 0.0.0.0:* users:((\"VLLM::EngineCor\",pid=547261,fd=40))"
GLOO_NODE_IP = "LISTEN 0 128 10.0.0.7:42725 0.0.0.0:* users:((\"VLLM::EngineCor\",pid=547261,fd=58))"
GLOO_LOOPBACK = "LISTEN 0 128 127.0.0.1:42725 0.0.0.0:* users:((\"VLLM::EngineCor\",pid=547261,fd=58))"
LOG = "INFO [parallel_state.py:1607] world_size=1 rank=0 distributed_init_method=tcp://127.0.0.1:36883 backend=nccl"


def test_rendezvous_port_parsed_from_vllm_log():
    assert rendezvous_port(LOG) == 36883
    assert rendezvous_port("nothing here") is None


def test_job_64330104_layout_fails():
    r = classify([HTTP, TCPSTORE] + [GLOO_NODE_IP] * 7, 48061, 36883)
    assert r["status"] == "fail"
    assert r["counts"] == {"loopback": 1, "rendezvous_tcpstore": 1, "unexpected": 7}


def test_only_tcpstore_beyond_loopback_is_unresolved_not_pass():
    r = classify([HTTP, TCPSTORE, GLOO_LOOPBACK], 48061, 36883)
    assert r["status"] == "unresolved"


def test_all_loopback_passes():
    assert classify([HTTP, GLOO_LOOPBACK], 48061, 36883)["status"] == "pass"


def test_ipv6_wildcard_tcpstore_is_still_only_unresolved():
    v6 = "LISTEN 0 128 [::]:36883 [::]:* users:((\"VLLM::EngineCor\",pid=547261,fd=41))"
    assert classify([HTTP, v6], 48061, 36883)["status"] == "unresolved"


def test_http_api_off_loopback_fails_even_if_port_matches_nothing_else():
    exposed = HTTP.replace("127.0.0.1:48061", "0.0.0.0:48061")
    assert classify([exposed], 48061, 36883)["status"] == "fail"


def test_unknown_rendezvous_port_means_wildcard_is_unexpected():
    assert classify([HTTP, TCPSTORE], 48061, None)["status"] == "fail"


def test_no_listeners_fails():
    assert classify([], 48061, 36883)["status"] == "fail"
