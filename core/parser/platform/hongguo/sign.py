"""红果播放、分集弹幕请求签名与视频内容密钥解码。"""

import base64
import binascii
import hashlib
import secrets
import struct
from typing import Dict

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def build_signature(query: str, body: bytes, now: float) -> Dict[str, str]:
    """根据实际传输的查询串和请求体生成签名头。

    Args:
        query: 已编码且不会再次改写的查询串。
        body: 实际发送的请求体字节。
        now: 当前 Unix 时间，单位为秒。

    Returns:
        播放接口所需的签名请求头。
    """
    timestamp = int(now) & 0xFFFFFFFF
    query_digest = hashlib.md5(query.encode("utf-8")).digest()
    body_digest = hashlib.md5(body).digest()
    payload = bytearray(
        query_digest[:4]
        + body_digest[:4]
        + bytes(4)
        + bytes((0, 6, 11, 28))
        + timestamp.to_bytes(4, "big")
    )
    mask = bytes.fromhex("44b9b9d9a4aef9fca493aa757ca3c2c4a496938f")
    for index, value in enumerate(mask):
        payload[index] ^= value
    # 混合过程依赖已经写回的首字节，不能改为同时计算全部字节。
    for index, value in enumerate(payload):
        rotated = ((value << 4) | (value >> 4)) & 0xFF
        mixed = rotated ^ payload[(index + 1) % len(payload)]
        reversed_bits = int(f"{mixed:08b}"[::-1], 2)
        payload[index] = reversed_bits ^ 0xFF ^ len(payload)
    return {
        "X-Khronos": str(timestamp),
        "X-Gorgon": (bytes.fromhex("8404401c0000") + payload).hex(),
        "X-SS-Req-Ticket": str(int(now * 1000)),
        "X-SS-STUB": body_digest.hex().upper(),
    }


def decode_video_key(spade: str) -> str:
    """解码播放接口提供的 AES-128 内容密钥。

    Args:
        spade: 播放源中的 spade_a 字段。

    Returns:
        32 位十六进制内容密钥。

    Raises:
        ValueError: 密钥编码、结构或版本无效。
    """
    if not isinstance(spade, str) or len(spade) > 1024:
        raise ValueError("红果媒体密钥数据无效")
    value = spade.strip()
    try:
        raw = base64.b64decode(value + "=" * (-len(value) % 4), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("红果媒体密钥编码无效") from exc
    if len(raw) < 3:
        raise ValueError("红果媒体密钥结构无效")
    tag_length = (raw[0] ^ raw[1] ^ raw[2]) - 48
    content_length = len(raw) - tag_length - 1
    if tag_length < 1 or content_length < 33 or content_length >= len(raw):
        raise ValueError("红果媒体密钥结构无效")
    seed = raw[-tag_length - 2] ^ raw[-tag_length - 1]
    tag = bytes(current ^ seed for current in raw[-tag_length:])
    if tag in (b"app_v2", b"web_v2"):
        raise ValueError("红果媒体密钥版本暂不支持")
    decoded = bytearray()
    previous = [250, 85]
    for index, current in enumerate(raw[1 : 1 + content_length]):
        slot = index % 2
        decoded.append(((previous[slot] ^ current) - 21 - index.bit_count()) & 0xFF)
        previous[slot] = current
    try:
        padding = int(chr(decoded[0]), 36)
        key = bytes.fromhex(decoded[1:33].decode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("红果媒体密钥内容无效") from exc
    if content_length - padding - 1 != 32 or len(key) != 16:
        raise ValueError("红果媒体密钥内容无效")
    return key.hex()


def _rol(value: int, count: int, width: int = 64) -> int:
    """在固定字宽内循环左移。"""
    count %= width
    return ((value << count) | (value >> (width - count))) & ((1 << width) - 1)


def _pad(data: bytes) -> bytes:
    """按十六字节块补齐协议数据。"""
    length = 16 - len(data) % 16
    return data + bytes([length]) * length


def _varint(value: int) -> bytes:
    """编码协议的无符号变长整数。"""
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _number(field: int, value: int) -> bytes:
    """编码整数协议字段。"""
    return _varint(field * 8) + _varint(value)


def _bytes(field: int, value: bytes) -> bytes:
    """编码字节串协议字段。"""
    return _varint(field * 8 + 2) + _varint(len(value)) + value


def _sm3(data: bytes) -> bytes:
    """使用既有密码库计算协议摘要。"""
    digest = hashes.Hash(hashes.SM3())
    digest.update(data)
    return digest.finalize()


def _ladon(aid: str, timestamp: int, prefix: bytes) -> str:
    """使用请求应用编号和对应签名域构造 Ladon。"""
    digest = hashlib.md5(prefix + aid.encode()).hexdigest().encode()
    left, right = struct.unpack('<QQ', digest[:16])
    queue = list(struct.unpack('<QQ', digest[16:]))
    keys = []
    mask = (1 << 64) - 1
    for index in range(34):
        keys.append(left)
        mixed = ((_rol(right, -8) + left) & mask) ^ index
        queue.append(mixed)
        left = mixed ^ _rol(left, 3)
        right = queue.pop(0)
    data = _pad(f'{timestamp}-1611921764-3019'.encode())
    result = bytearray()
    for offset in range(0, len(data), 16):
        left, right = struct.unpack('<QQ', data[offset:offset + 16])
        for key in keys:
            right = key ^ ((left + _rol(right, -8)) & mask)
            left = right ^ _rol(left, 3)
        result.extend(struct.pack('<QQ', left, right))
    return base64.b64encode(prefix + result).decode()


def _comment_argus(query: str, timestamp: int, nonce: bytes) -> str:
    """编码评论请求的独立 Argus 字段。"""
    key = bytes.fromhex('ac1adaae95a7af94a5114ab3b3a97dd80050aa0a39314c40528caec95256c28c')
    message = _number(1, 0x20200929 * 2) + _number(2, 2) + _number(3, int.from_bytes(nonce[6:10], 'little'))
    for field, value in ((4, b'3019'), (6, b'1611921764'), (7, b'6.8.1.32'), (8, b'v04.07.01-ml-android'), (10, bytes(8))):
        message += _bytes(field, value)
    message += _number(9, 135135744) + _number(12, timestamp * 2)
    message += _bytes(13, _sm3(bytes(16))[:6]) + _bytes(14, _sm3(query.encode())[:6])
    nested = _number(1, 2 + (nonce[10] % 49) * 2)
    nested += b''.join(_number(field, 1388734) for field in (2, 3, 4))
    message += _bytes(15, nested) + _bytes(20, b'none') + _number(21, 738)
    return _encrypt_argus(message, key)


def _encrypt_argus(message: bytes, key: bytes) -> str:
    """封装该平台评论协议的 Argus 密文。"""
    data = _pad(message)
    rounds = list(struct.unpack('<QQQQ', _sm3(key + bytes.fromhex('3ccc567b') + key)))
    mask = (1 << 64) - 1
    for index in range(4, 72):
        mixed = _rol(rounds[index - 1], -3) ^ rounds[index - 3]
        mixed ^= _rol(mixed, -1)
        rounds.append(((~rounds[index - 4]) ^ mixed ^ ((0x3dc94c3a046d678b >> ((index - 4) % 62)) & 1) ^ 3) & mask)
    encoded = bytearray()
    for offset in range(0, len(data), 16):
        left, right = struct.unpack('<QQ', data[offset:offset + 16])
        for value in rounds:
            left, right = right, left ^ (_rol(right, 1) & _rol(right, 8)) ^ _rol(right, 2) ^ value
        encoded.extend(struct.pack('<QQ', left, right))
    xor = bytes.fromhex('d04ffdffd04ffdff')
    mixed = xor + bytes(value ^ xor[index % 8] for index, value in enumerate(encoded))
    container = _pad(bytes.fromhex('a6e783ee7001100918') + mixed[::-1] + bytes.fromhex('567b'))
    encryptor = Cipher(algorithms.AES(hashlib.md5(key[:16]).digest()), modes.CBC(hashlib.md5(key[16:]).digest())).encryptor()
    return base64.b64encode(bytes.fromhex('3ccc') + encryptor.update(container) + encryptor.finalize()).decode()


def build_comment_signature(query: str, now: float) -> Dict[str, str]:
    """生成分集弹幕的评论专用签名。

    Args:
        query: 实际传输、不会再次编码的查询串。
        now: 当前 Unix 时间。

    Returns:
        包含空正文摘要和评论专用 Argus、Ladon 的请求头。
    """
    nonce = secrets.token_bytes(11)
    timestamp = int(now) & 0xffffffff
    head2, head3 = (nonce[0] % 7 + 1) << 5, 0xe0 | (nonce[1] & 15)
    seed = bytearray(hashlib.md5(query.encode()).digest()[:4] + bytes(8) + bytes((0, 1, 7, 4)) + timestamp.to_bytes(4, 'big'))
    key = bytes((0x4a, 0, 0x16, head3, 0x47, 0x6c, 0, head2))
    box = list(range(256))
    index_j = 0
    for index in range(256):
        index_j = (index_j + box[index] + key[index & 7]) & 255
        box[index] = box[index_j]
    index_j = 0
    for offset in range(len(seed)):
        index = offset + 1
        index_j = (index_j + box[index]) & 255
        box[index] = box[index_j]
        seed[offset] ^= box[(box[index] + box[index_j]) & 255]
    for index in range(len(seed)):
        mixed = _rol(seed[index], 4, 8) ^ seed[(index + 1) % len(seed)]
        seed[index] = int(f'{mixed:08b}'[::-1], 2) ^ 0xeb
    return {
        "X-Gorgon": (bytes((0x84, 4, head2, head3, 0, 0)) + seed).hex(),
        "X-Khronos": str(timestamp),
        "X-SS-Req-Ticket": str(int(now * 1000)),
        "X-SS-STUB": "",
        "X-Argus": _comment_argus(query, timestamp, nonce),
        "X-Ladon": _ladon("8662", timestamp, nonce[2:6]),
    }
