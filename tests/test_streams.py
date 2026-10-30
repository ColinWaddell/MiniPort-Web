"""AudioReceiver tests with datagrams fed by a local UDP sender."""

import socket

from support import make_audio_datagram, wait_for

from eb200.streams import AudioReceiver


def _sender():
    return socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def test_receives_continuous_datagrams():
    rx = AudioReceiver()
    sender = _sender()
    rx.start()
    target = ("127.0.0.1", rx.port)
    try:
        for seq, base in ((10, 100), (11, 200), (12, 300)):
            sender.sendto(
                make_audio_datagram(seq, [base + i for i in range(4)]), target
            )
        assert wait_for(lambda: rx.stats()["samples"] >= 12)
        samples = rx.read(1024)
        assert list(samples) == [
            100,
            101,
            102,
            103,
            200,
            201,
            202,
            203,
            300,
            301,
            302,
            303,
        ]
        stats = rx.stats()
        assert stats["packets"] == 3
        assert stats["lost"] == 0
    finally:
        rx.stop()
        sender.close()


def test_sequence_gap_is_healed_with_silence():
    rx = AudioReceiver()
    sender = _sender()
    rx.start()
    target = ("127.0.0.1", rx.port)
    try:
        sender.sendto(make_audio_datagram(10, [100, 101]), target)
        assert wait_for(lambda: rx.stats()["samples"] >= 2)
        # Jump from 10 to 13: packets 11 and 12 (2 samples each) are lost.
        sender.sendto(make_audio_datagram(13, [300, 301]), target)
        assert wait_for(lambda: rx.stats()["samples"] >= 8)
        samples = rx.read(1024)
        assert list(samples) == [100, 101, 0, 0, 0, 0, 300, 301]
        stats = rx.stats()
        assert stats["lost"] == 2
    finally:
        rx.stop()
        sender.close()


def test_duplicate_sequence_is_counted_not_replayed():
    rx = AudioReceiver()
    sender = _sender()
    rx.start()
    target = ("127.0.0.1", rx.port)
    try:
        sender.sendto(make_audio_datagram(10, [100, 101]), target)
        sender.sendto(make_audio_datagram(10, [100, 101]), target)
        assert wait_for(lambda: rx.stats()["duplicates"] >= 1)
        assert wait_for(lambda: rx.stats()["packets"] >= 1)
        samples = rx.read(1024)
        assert list(samples) == [100, 101]
        stats = rx.stats()
        assert stats["duplicates"] == 1
    finally:
        rx.stop()
        sender.close()


def test_receiver_sequence_restart_is_not_loss():
    rx = AudioReceiver()
    sender = _sender()
    rx.start()
    target = ("127.0.0.1", rx.port)
    try:
        sender.sendto(make_audio_datagram(10, [100, 101]), target)
        assert wait_for(lambda: rx.stats()["samples"] >= 2)
        # The receiver restarted its sequence counter (e.g. after a state
        # change): treat as alignment, not as thousands of lost packets.
        sender.sendto(make_audio_datagram(60000, [300, 301]), target)
        assert wait_for(lambda: rx.stats()["samples"] >= 4)
        stats = rx.stats()
        assert stats["lost"] == 0
        assert stats["resets"] == 1
        samples = rx.read(1024)
        assert list(samples) == [100, 101, 300, 301]
    finally:
        rx.stop()
        sender.close()


def test_parse_errors_are_counted():
    rx = AudioReceiver()
    sender = _sender()
    rx.start()
    target = ("127.0.0.1", rx.port)
    try:
        sender.sendto(b"not-an-eb200-datagram", target)
        assert wait_for(lambda: rx.stats()["parse_errors"] >= 1)
    finally:
        rx.stop()
        sender.close()
