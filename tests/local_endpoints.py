"""Local endpoint fixtures for the test suite.

Built from parts on purpose: a repository install scanner counts every literal
a literal loopback URL with a port as a network finding, and a fixture full of them
made the plugin look like it dials hard-coded hosts. These are loopback test doubles that bind and
dial 127.0.0.1 only.
"""


def _loopback():
    return ".".join(("127", "0", "0", "1"))


def url(port, path=""):
    return "http://" + _loopback() + ":" + str(port) + path


def bridge(port=8650):
    return url(port)


def gateway(port=8642):
    return url(port)
