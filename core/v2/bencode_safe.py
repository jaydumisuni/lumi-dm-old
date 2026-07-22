"""Bounded bencode decoder for untrusted torrent metadata."""
from __future__ import annotations

from typing import Any


class BencodeError(ValueError):
    pass


def bdecode(
    data: bytes,
    *,
    max_bytes: int = 8 * 1024 * 1024,
    max_depth: int = 64,
    max_items: int = 1_000_000,
    max_string_bytes: int = 8 * 1024 * 1024,
) -> Any:
    if not isinstance(data, bytes):
        raise BencodeError("Bencode input must be bytes")
    if len(data) > max_bytes:
        raise BencodeError(f"Bencode input exceeds {max_bytes} bytes")

    index = 0
    item_count = 0

    def require(condition: bool, message: str) -> None:
        if not condition:
            raise BencodeError(message)

    def parse(depth: int) -> Any:
        nonlocal index, item_count
        require(depth <= max_depth, "Bencode nesting limit exceeded")
        require(index < len(data), "Unexpected end of bencode data")
        item_count += 1
        require(item_count <= max_items, "Bencode item limit exceeded")
        token = data[index:index + 1]

        if token == b"i":
            index += 1
            end = data.find(b"e", index)
            require(end >= 0, "Unterminated integer")
            raw = data[index:end]
            require(bool(raw), "Empty integer")
            require(raw != b"-0", "Negative zero is invalid")
            require(
                not (raw.startswith(b"0") and len(raw) > 1)
                and not (raw.startswith(b"-0") and len(raw) > 2),
                "Integer has a leading zero",
            )
            try:
                value = int(raw)
            except ValueError as exc:
                raise BencodeError("Invalid integer") from exc
            index = end + 1
            return value

        if token == b"l":
            index += 1
            values = []
            while True:
                require(index < len(data), "Unterminated list")
                if data[index:index + 1] == b"e":
                    index += 1
                    return values
                values.append(parse(depth + 1))

        if token == b"d":
            index += 1
            values: dict[bytes, Any] = {}
            previous: bytes | None = None
            while True:
                require(index < len(data), "Unterminated dictionary")
                if data[index:index + 1] == b"e":
                    index += 1
                    return values
                key = parse(depth + 1)
                require(isinstance(key, bytes), "Dictionary key must be bytes")
                require(key not in values, "Duplicate dictionary key")
                require(previous is None or key > previous, "Dictionary keys are unsorted")
                previous = key
                values[key] = parse(depth + 1)

        require(token.isdigit(), f"Unexpected bencode token: {token!r}")
        colon = data.find(b":", index)
        require(colon >= 0, "Invalid byte string")
        raw_length = data[index:colon]
        require(bool(raw_length), "Missing byte string length")
        require(
            not (raw_length.startswith(b"0") and len(raw_length) > 1),
            "Byte string length has a leading zero",
        )
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise BencodeError("Invalid byte string length") from exc
        require(0 <= length <= max_string_bytes, "Byte string length exceeds limit")
        index = colon + 1
        end = index + length
        require(end <= len(data), "Truncated byte string")
        value = data[index:end]
        index = end
        return value

    value = parse(0)
    require(index == len(data), "Trailing bencode data")
    return value
