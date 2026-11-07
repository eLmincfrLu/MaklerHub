"""client_ip() — bazasız unit testlər. Fixture istifadə etmir, ayrıca işləyir:
    pytest tests/test_client_ip.py -p no:cacheprovider
"""
import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from maklerhub.auth import client_ip, client_ip_source  # noqa: E402

app = Flask(__name__)  # create_app yox — bazaya qoşulmasın
REAL = "77.244.119.164"
RENDER = "10.197.71.38"
CF_EDGE = "162.158.111.145"


def ip_for(headers, remote=RENDER):
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": remote}):
        return client_ip_source()


@pytest.fixture
def proxy(monkeypatch):
    monkeypatch.setenv("MAKLERHUB_BEHIND_PROXY", "1")


@pytest.fixture
def local(monkeypatch):
    monkeypatch.delenv("MAKLERHUB_BEHIND_PROXY", raising=False)


def test_cf_connecting_ip_first(proxy):
    headers = {"CF-Connecting-IP": REAL, "True-Client-IP": "1.1.1.1",
               "X-Forwarded-For": f"{REAL}, {CF_EDGE}, {RENDER}"}
    assert ip_for(headers) == (REAL, "CF-Connecting-IP")


def test_true_client_ip_when_no_cf(proxy):
    assert ip_for({"True-Client-IP": REAL}) == (REAL, "True-Client-IP")


def test_remote_addr_when_no_headers(proxy):
    assert ip_for({}) == (RENDER, "remote_addr")
    # XFF özü heç vaxt istifadə olunmur
    assert ip_for({"X-Forwarded-For": "6.6.6.6"}) == (RENDER, "remote_addr")


@pytest.mark.parametrize("bad", ["", "   ", "abc", "1.2.3", "999.1.1.1", "1.2.3.4, 5.6.7.8", "<script>"])
def test_invalid_value_falls_through(proxy, bad):
    assert ip_for({"CF-Connecting-IP": bad, "True-Client-IP": REAL}) == (REAL, "True-Client-IP")
    assert ip_for({"CF-Connecting-IP": bad}) == (RENDER, "remote_addr")


def test_ipv6_and_whitespace_normalized(proxy):
    assert ip_for({"CF-Connecting-IP": " 2a02:4780:0:0::1 "}) == ("2a02:4780::1", "CF-Connecting-IP")


def test_headers_ignored_without_behind_proxy(local):
    headers = {"CF-Connecting-IP": "6.6.6.6", "True-Client-IP": "7.7.7.7", "X-Forwarded-For": "8.8.8.8"}
    assert ip_for(headers, remote="127.0.0.1") == ("127.0.0.1", "remote_addr")
    with app.test_request_context("/", headers=headers, environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        assert client_ip() == "127.0.0.1"


def test_other_env_values_are_not_proxy_mode(monkeypatch):
    monkeypatch.setenv("MAKLERHUB_BEHIND_PROXY", "true")
    assert ip_for({"CF-Connecting-IP": REAL}) == (RENDER, "remote_addr")
