"""知乎文章接口请求签名，使用标准库实现。"""

import hashlib
import re
import secrets
from typing import Optional


# 算法与知乎网页端使用的 x-zse-96 v3 兼容。
ROUND_KEYS = (
    1170614578, 1024848638, 1413669199, -343334464, -766094290,
    -1373058082, -143119608, -297228157, 1933479194, -971186181,
    -406453910, 460404854, -547427574, -1891326262, -1679095901,
    2119585428, -2029270069, 2035090028, -1521520070, -5587175,
    -77751101, -2094365853, -1243052806, 1579901135, 1321810770,
    456816404, -1391643889, -229302305, 330002838, -788960546,
    363569021, -1947871109,
)
SUBSTITUTION = (
    20, 223, 245, 7, 248, 2, 194, 209, 87, 6, 227, 253, 240, 128, 222, 91,
    237, 9, 125, 157, 230, 93, 252, 205, 90, 79, 144, 199, 159, 197, 186, 167,
    39, 37, 156, 198, 38, 42, 43, 168, 217, 153, 15, 103, 80, 189, 71, 191,
    97, 84, 247, 95, 36, 69, 14, 35, 12, 171, 28, 114, 178, 148, 86, 182,
    32, 83, 158, 109, 22, 255, 94, 238, 151, 85, 77, 124, 254, 18, 4, 26,
    123, 176, 232, 193, 131, 172, 143, 142, 150, 30, 10, 146, 162, 62, 224, 218,
    196, 229, 1, 192, 213, 27, 110, 56, 231, 180, 138, 107, 242, 187, 54, 120,
    19, 44, 117, 228, 215, 203, 53, 239, 251, 127, 81, 11, 133, 96, 204, 132,
    41, 115, 73, 55, 249, 147, 102, 48, 122, 145, 106, 118, 74, 190, 29, 16,
    174, 5, 177, 129, 63, 113, 99, 31, 161, 76, 246, 34, 211, 13, 60, 68,
    207, 160, 65, 111, 82, 165, 67, 169, 225, 57, 112, 244, 155, 51, 236, 200,
    233, 58, 61, 47, 100, 137, 185, 64, 17, 70, 234, 163, 219, 108, 170, 166,
    59, 149, 52, 105, 24, 212, 78, 173, 45, 0, 116, 226, 119, 136, 206, 135,
    175, 195, 25, 92, 121, 208, 126, 139, 3, 75, 141, 21, 130, 98, 241, 40,
    154, 66, 184, 49, 181, 46, 243, 88, 101, 183, 8, 23, 72, 188, 104, 179,
    210, 134, 250, 201, 164, 89, 216, 202, 220, 50, 221, 152, 140, 33, 235, 214,
)
ENCODING_ALPHABET = '6fpLRqJO8M/c3jnYxFkUVC4ZIG12SiH=5v0mXDazWBTsuw7QetbKdoPyAl+hN9rgE'
INITIAL_MASK = b'059053f7d15e01d7'
WORD_MASK = 0xFFFFFFFF


def _rotate_left(value: int, shift: int) -> int:
    """按照 JavaScript 位运算语义循环左移一个三十二位整数。"""
    value &= WORD_MASK
    return ((value << shift) | (value >> (32 - shift))) & WORD_MASK


def _substitute_and_mix(value: int) -> int:
    """执行四字节替换和轮变换。"""
    substituted = 0
    for shift in (24, 16, 8, 0):
        substituted = (substituted << 8) | SUBSTITUTION[(value >> shift) & 255]
    mixed = substituted
    for shift in (2, 10, 18, 24):
        mixed ^= _rotate_left(substituted, shift)
    return mixed & WORD_MASK


def _encrypt_block(block: bytes) -> bytes:
    """使用固定轮密钥转换一个十六字节分组。"""
    words = [int.from_bytes(block[index:index + 4], 'big') for index in range(0, 16, 4)]
    for index, key in enumerate(ROUND_KEYS):
        mixed = words[index + 1] ^ words[index + 2] ^ words[index + 3] ^ key
        words.append((words[index] ^ _substitute_and_mix(mixed)) & WORD_MASK)
    return b''.join(value.to_bytes(4, 'big') for value in reversed(words[-4:]))


def _preprocess_digest(md5_hex: str, random_prefix: int) -> bytes:
    """构造固定长度消息并链式处理三个分组。"""
    payload = bytes((random_prefix, 0)) + md5_hex.encode('ascii') + bytes((14,)) * 15
    first = bytes(value ^ mask ^ 42 for value, mask in zip(payload[:16], INITIAL_MASK))
    previous = _encrypt_block(first)
    output = bytearray(previous)
    for start in (16, 32):
        mixed = bytes(value ^ mask for value, mask in zip(payload[start:start + 16], previous))
        previous = _encrypt_block(mixed)
        output.extend(previous)
    return bytes(output)


def encrypt_md5(md5_hex: str, random_prefix: Optional[int] = None) -> str:
    """按照公开算法加密三十二字符的 MD5 摘要。

    Args:
        md5_hex: 十六进制格式的 MD5 摘要。
        random_prefix: 可选的零至一百二十六前缀，仅用于确定性离线对照。

    Returns:
        不含协议前缀的六十四字符签名。
    """
    if re.fullmatch(r'[0-9a-fA-F]{32}', md5_hex) is None:
        raise ValueError('MD5 摘要必须是三十二个十六进制字符')
    if random_prefix is None:
        random_prefix = secrets.randbelow(127)
    if not isinstance(random_prefix, int) or not 0 <= random_prefix <= 126:
        raise ValueError('随机前缀必须是零至一百二十六之间的整数')
    processed = _preprocess_digest(md5_hex, random_prefix)
    current = 0
    output = []
    for index, value in enumerate(reversed(processed)):
        transformed = value ^ ((58 >> (8 * (index % 4))) & 255)
        current |= transformed << (8 * (index % 3))
        if index % 3 == 2:
            output.extend(ENCODING_ALPHABET[(current >> shift) & 63] for shift in (0, 6, 12, 18))
            current = 0
    return ''.join(output)


def sign_article(api_path: str, dc0: str, random_prefix: Optional[int] = None) -> str:
    """根据请求路径与匿名 Cookie 值生成文章接口签名。

    Args:
        api_path: 包括查询参数的接口路径，必须与实际请求保持一致。
        dc0: 匿名 d_c0 Cookie 的原始值。
        random_prefix: 可选的确定性随机前缀，正常调用时省略。

    Returns:
        可用于 x-zse-96 请求头的签名字符串。
    """
    message = '101_3_3.0+' + api_path + '+' + dc0
    digest = hashlib.md5(message.encode('utf-8')).hexdigest()
    return '2.0_' + encrypt_md5(digest, random_prefix)
