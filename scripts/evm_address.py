"""Dependency-free EIP-55 checks. Keccak-256 is NOT hashlib.sha3_256."""
import re

MASK = (1 << 64) - 1
ROT = ((0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
       (28, 55, 25, 21, 56), (27, 20, 39, 8, 14))
RC = (0x1, 0x8082, 0x800000000000808A, 0x8000000080008000, 0x808B,
      0x80000001, 0x8000000080008081, 0x8000000000008009, 0x8A, 0x88,
      0x80008009, 0x8000000A, 0x8000808B, 0x800000000000008B,
      0x8000000000008089, 0x8000000000008003, 0x8000000000008002,
      0x8000000000000080, 0x800A, 0x800000008000000A, 0x8000000080008081,
      0x8000000000008080, 0x80000001, 0x8000000080008008)
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


def _rot(n, bits):
    return ((n << bits) | (n >> (64 - bits))) & MASK


def keccak256(data: bytes) -> bytes:
    """Keccak-f[1600], 1088-bit rate, original Keccak domain suffix 0x01."""
    rate = 136
    padded = bytearray(data)
    padding = rate - len(padded) % rate
    padded.extend(b"\x00" * padding)
    padded[len(data)] = 0x01
    padded[-1] |= 0x80
    state = [0] * 25
    for offset in range(0, len(padded), rate):
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(padded[offset + i * 8:offset + i * 8 + 8], "little")
        for constant in RC:
            columns = [state[x] ^ state[x+5] ^ state[x+10] ^ state[x+15] ^ state[x+20] for x in range(5)]
            for x in range(5):
                delta = columns[(x-1) % 5] ^ _rot(columns[(x+1) % 5], 1)
                for y in range(5):
                    state[x+5*y] ^= delta
            b = [0] * 25
            for x in range(5):
                for y in range(5):
                    b[y + 5*((2*x+3*y) % 5)] = _rot(state[x+5*y], ROT[x][y])
            for x in range(5):
                for y in range(5):
                    state[x+5*y] = b[x+5*y] ^ ((~b[(x+1) % 5+5*y]) & b[(x+2) % 5+5*y])
            state[0] ^= constant
    return b"".join(value.to_bytes(8, "little") for value in state)[:32]


def checksum_address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS.fullmatch(value):
        raise ValueError("Use a full 0x-prefixed EVM address")
    lower = value[2:].lower()
    hashed = keccak256(lower.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(hashed[i], 16) >= 8 else c for i, c in enumerate(lower))


def validate_address(value: str) -> str:
    if not isinstance(value, str) or not ADDRESS.fullmatch(value):
        raise ValueError("Use a full 0x-prefixed EVM address")
    if int(value, 16) in {0, 0xDEAD}:
        raise ValueError("Zero and known burn addresses are not valid payment addresses")
    letters = value[2:]
    if letters != letters.lower() and letters != letters.upper() and value != checksum_address(value):
        raise ValueError("Invalid EIP-55 checksum. Verify the original address; do not lowercase to bypass it.")
    return value.lower()
