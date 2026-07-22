"""Hardened public torrent facade.

The Wave 3 torrent engine is reused, but its metadata decoder is replaced at module
scope so every inspection path receives bounded, typed failures for untrusted input.
"""
from . import torrents as _base
from .bencode_safe import BencodeError, bdecode

_base.BencodeError = BencodeError
_base.bdecode = bdecode

TorrentUnavailable = _base.TorrentUnavailable
TorrentPlan = _base.TorrentPlan
TorrentService = _base.TorrentService
bencode = _base.bencode
inspect_torrent = _base.inspect_torrent

__all__ = [
    "BencodeError",
    "TorrentPlan",
    "TorrentService",
    "TorrentUnavailable",
    "bdecode",
    "bencode",
    "inspect_torrent",
]
