# -*- coding: utf-8 -*-
# SPDX-License-Identifier: Apache-2.0
"""
抖音 Web 端 a_bogus 签名生成（纯 Python，无第三方依赖）。

移植自 Johnserf-Seed/f2 (Apache License 2.0)
- https://github.com/Johnserf-Seed/f2
- 原始文件: f2/utils/abogus.py

改动说明:
- 移除 gmssl 依赖，内嵌纯 Python 的 SM3 实现（GB/T 32905-2016）。
- 对外只保留 generate_abogus() 入口，其余为内部实现。

用法:
    from .douyin_sign import generate_abogus
    sig = generate_abogus(param_str, body="", user_agent=ua, options=[0, 1, 14])
"""

import random
import time
from typing import List, Optional, Union

# ---------------------------------------------------------------------------
# 纯 Python SM3 (GB/T 32905-2016)
# ---------------------------------------------------------------------------

_IV = [
    0x7380166F,
    0x4914B2B9,
    0x172442D7,
    0xDA8A0600,
    0xA96F30BC,
    0x163138AA,
    0xE38DEE4D,
    0xB0FB0E4E,
]

_TJ = [0x79CC4519, 0x7A879D8A]


def _rotl(x: int, n: int) -> int:
    n %= 32
    if n == 0:
        return x & 0xFFFFFFFF
    return ((x << n) | (x >> (32 - n))) & 0xFFFFFFFF


def _sm3_compress(state: List[int], block: bytes) -> List[int]:
    w = [0] * 68
    for i in range(16):
        w[i] = int.from_bytes(block[i * 4 : i * 4 + 4], "big")
    for i in range(16, 68):
        x = w[i - 16] ^ w[i - 9] ^ _rotl(w[i - 3], 15)
        w[i] = (
            x ^ _rotl(x, 15) ^ _rotl(x, 23) ^ _rotl(w[i - 13], 7) ^ w[i - 6]
        ) & 0xFFFFFFFF
    w1 = [0] * 64
    for i in range(64):
        w1[i] = (w[i] ^ w[i + 4]) & 0xFFFFFFFF

    a, b, c, d, e, f, g, h = state
    for j in range(64):
        t = 0 if j < 16 else 1
        ss1 = _rotl((_rotl(a, 12) + e + _rotl(_TJ[t], j)) & 0xFFFFFFFF, 7)
        ss2 = ss1 ^ _rotl(a, 12)
        if t == 0:
            tt1 = (a ^ b ^ c) + d + ss2 + w1[j]
            tt2 = (e ^ f ^ g) + h + ss1 + w[j]
        else:
            tt1 = ((a & b) | (a & c) | (b & c)) + d + ss2 + w1[j]
            tt2 = ((e & f) | ((~e) & g)) + h + ss1 + w[j]
        tt1 &= 0xFFFFFFFF
        tt2 &= 0xFFFFFFFF
        d, c, b, a = c, _rotl(b, 9), a, tt1
        h, g, f, e = (
            g,
            _rotl(f, 19),
            e,
            (tt2 ^ _rotl(tt2, 9) ^ _rotl(tt2, 17)) & 0xFFFFFFFF,
        )
    return [
        (state[0] ^ a) & 0xFFFFFFFF,
        (state[1] ^ b) & 0xFFFFFFFF,
        (state[2] ^ c) & 0xFFFFFFFF,
        (state[3] ^ d) & 0xFFFFFFFF,
        (state[4] ^ e) & 0xFFFFFFFF,
        (state[5] ^ f) & 0xFFFFFFFF,
        (state[6] ^ g) & 0xFFFFFFFF,
        (state[7] ^ h) & 0xFFFFFFFF,
    ]


def sm3_digest(data: bytes) -> bytes:
    """返回 data 的 32 字节 SM3 摘要。"""
    state = _IV[:]
    msg = bytearray(data)
    bit_len = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg += bit_len.to_bytes(8, "big")
    for i in range(0, len(msg), 64):
        state = _sm3_compress(state, bytes(msg[i : i + 64]))
    return b"".join(x.to_bytes(4, "big") for x in state)


# ---------------------------------------------------------------------------
# a_bogus 算法（移植自 f2，Apache-2.0）
# ---------------------------------------------------------------------------


class _StringProcessor:
    @staticmethod
    def to_char_array(s: str) -> List[int]:
        return [ord(c) for c in s]

    @staticmethod
    def to_char_str(byte_list: List[int]) -> str:
        return "".join(chr(b) for b in byte_list)

    @staticmethod
    def js_shift_right(val: int, n: int) -> int:
        return (val % 0x100000000) >> n

    @staticmethod
    def generate_random_bytes(length: int = 3) -> str:
        def generate_byte_sequence() -> List[str]:
            rd = int(random.random() * 10000)
            return [
                chr((rd & 255 & 170) | 1),
                chr((rd & 255 & 85) | 2),
                chr((_StringProcessor.js_shift_right(rd, 8) & 170) | 5),
                chr((_StringProcessor.js_shift_right(rd, 8) & 85) | 40),
            ]

        result = []
        for _ in range(length):
            result.extend(generate_byte_sequence())
        return "".join(result)


class _CryptoUtility:
    def __init__(self, salt: str, custom_base64_alphabet: List[str]):
        self.salt = salt
        self.base64_alphabet = custom_base64_alphabet
        self.big_array = [
            121,
            243,
            55,
            234,
            103,
            36,
            47,
            228,
            30,
            231,
            106,
            6,
            115,
            95,
            78,
            101,
            250,
            207,
            198,
            50,
            139,
            227,
            220,
            105,
            97,
            143,
            34,
            28,
            194,
            215,
            18,
            100,
            159,
            160,
            43,
            8,
            169,
            217,
            180,
            120,
            247,
            45,
            90,
            11,
            27,
            197,
            46,
            3,
            84,
            72,
            5,
            68,
            62,
            56,
            221,
            75,
            144,
            79,
            73,
            161,
            178,
            81,
            64,
            187,
            134,
            117,
            186,
            118,
            16,
            241,
            130,
            71,
            89,
            147,
            122,
            129,
            65,
            40,
            88,
            150,
            110,
            219,
            199,
            255,
            181,
            254,
            48,
            4,
            195,
            248,
            208,
            32,
            116,
            167,
            69,
            201,
            17,
            124,
            125,
            104,
            96,
            83,
            80,
            127,
            236,
            108,
            154,
            126,
            204,
            15,
            20,
            135,
            112,
            158,
            13,
            1,
            188,
            164,
            210,
            237,
            222,
            98,
            212,
            77,
            253,
            42,
            170,
            202,
            26,
            22,
            29,
            182,
            251,
            10,
            173,
            152,
            58,
            138,
            54,
            141,
            185,
            33,
            157,
            31,
            252,
            132,
            233,
            235,
            102,
            196,
            191,
            223,
            240,
            148,
            39,
            123,
            92,
            82,
            128,
            109,
            57,
            24,
            38,
            113,
            209,
            245,
            2,
            119,
            153,
            229,
            189,
            214,
            230,
            174,
            232,
            63,
            52,
            205,
            86,
            140,
            66,
            175,
            111,
            171,
            246,
            133,
            238,
            193,
            99,
            60,
            74,
            91,
            225,
            51,
            76,
            37,
            145,
            211,
            166,
            151,
            213,
            206,
            0,
            200,
            244,
            176,
            218,
            44,
            184,
            172,
            49,
            216,
            93,
            168,
            53,
            21,
            183,
            41,
            67,
            85,
            224,
            155,
            226,
            242,
            87,
            177,
            146,
            70,
            190,
            12,
            162,
            19,
            137,
            114,
            25,
            165,
            163,
            192,
            23,
            59,
            9,
            94,
            179,
            107,
            35,
            7,
            142,
            131,
            239,
            203,
            149,
            136,
            61,
            249,
            14,
            156,
        ]

    @staticmethod
    def sm3_to_array(input_data: Union[str, List[int]]) -> List[int]:
        if isinstance(input_data, str):
            data = input_data.encode("utf-8")
        else:
            data = bytes(input_data)
        return list(sm3_digest(data))

    def add_salt(self, param: str) -> str:
        return param + self.salt

    def params_to_array(
        self, param: Union[str, List[int]], add_salt: bool = True
    ) -> List[int]:
        if isinstance(param, str) and add_salt:
            param = self.add_salt(param)
        return self.sm3_to_array(param)

    def transform_bytes(self, bytes_list: List[int]) -> str:
        bytes_str = _StringProcessor.to_char_str(bytes_list)
        result_str = []
        index_b = self.big_array[1]
        initial_value = 0
        value_e = 0
        for index, char in enumerate(bytes_str):
            if index == 0:
                initial_value = self.big_array[index_b]
                sum_initial = index_b + initial_value
                self.big_array[1] = initial_value
                self.big_array[index_b] = index_b
            else:
                sum_initial = initial_value + value_e
            char_value = ord(char)
            sum_initial %= len(self.big_array)
            value_f = self.big_array[sum_initial]
            result_str.append(chr(char_value ^ value_f))
            value_e = self.big_array[(index + 2) % len(self.big_array)]
            sum_initial = (index_b + value_e) % len(self.big_array)
            initial_value = self.big_array[sum_initial]
            self.big_array[sum_initial] = self.big_array[
                (index + 2) % len(self.big_array)
            ]
            self.big_array[(index + 2) % len(self.big_array)] = initial_value
            index_b = sum_initial
        return "".join(result_str)

    def base64_encode(self, input_string: str, selected_alphabet: int = 0) -> str:
        binary_string = "".join("{:08b}".format(ord(c)) for c in input_string)
        padding_length = (6 - len(binary_string) % 6) % 6
        binary_string += "0" * padding_length
        indices = [
            int(binary_string[i : i + 6], 2) for i in range(0, len(binary_string), 6)
        ]
        output = "".join(
            self.base64_alphabet[selected_alphabet][idx] for idx in indices
        )
        output += "=" * (padding_length // 2)
        return output

    def abogus_encode(self, abogus_bytes_str: str, selected_alphabet: int) -> str:
        abogus = []
        for i in range(0, len(abogus_bytes_str), 3):
            if i + 2 < len(abogus_bytes_str):
                n = (
                    (ord(abogus_bytes_str[i]) << 16)
                    | (ord(abogus_bytes_str[i + 1]) << 8)
                    | ord(abogus_bytes_str[i + 2])
                )
            elif i + 1 < len(abogus_bytes_str):
                n = (ord(abogus_bytes_str[i]) << 16) | (
                    ord(abogus_bytes_str[i + 1]) << 8
                )
            else:
                n = ord(abogus_bytes_str[i]) << 16
            for j, k in zip(range(18, -1, -6), (0xFC0000, 0x03F000, 0x0FC0, 0x3F)):
                if j == 6 and i + 1 >= len(abogus_bytes_str):
                    break
                if j == 0 and i + 2 >= len(abogus_bytes_str):
                    break
                abogus.append(self.base64_alphabet[selected_alphabet][(n & k) >> j])
        abogus.append("=" * ((4 - len(abogus) % 4) % 4))
        return "".join(abogus)

    @staticmethod
    def rc4_encrypt(key: bytes, plaintext: str) -> bytes:
        s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + s[i] + key[i % len(key)]) % 256
            s[i], s[j] = s[j], s[i]
        i = j = 0
        ciphertext = []
        for char in plaintext:
            i = (i + 1) % 256
            j = (j + s[i]) % 256
            s[i], s[j] = s[j], s[i]
            ciphertext.append(ord(char) ^ s[(s[i] + s[j]) % 256])
        return bytes(ciphertext)


class _BrowserFingerprintGenerator:
    @classmethod
    def generate_fingerprint(cls, browser_type: str = "Edge") -> str:
        platform = "MacIntel" if browser_type == "Safari" else "Win32"
        inner_width = random.randint(1024, 1920)
        inner_height = random.randint(768, 1080)
        outer_width = inner_width + random.randint(24, 32)
        outer_height = inner_height + random.randint(75, 90)
        screen_y = random.choice([0, 30])
        size_width = random.randint(1024, 1920)
        size_height = random.randint(768, 1080)
        avail_width = random.randint(1280, 1920)
        avail_height = random.randint(800, 1080)
        return (
            f"{inner_width}|{inner_height}|{outer_width}|{outer_height}|0|"
            f"{screen_y}|0|0|{size_width}|{size_height}|{avail_width}|"
            f"{avail_height}|{inner_width}|{inner_height}|24|24|{platform}"
        )


class _ABogus:
    def __init__(
        self,
        fp: str = "",
        user_agent: str = "",
        options: Optional[List[int]] = None,
    ):
        self.aid = 6383
        self.page_id = 0
        self.salt = "cus"
        self.boe = False
        self.ddrt = 8.5
        self.ic = 8.5
        self.paths = [
            "^/webcast/",
            "^/aweme/v1/",
            "^/aweme/v2/",
            "/v1/message/send",
            "^/live/",
            "^/captcha/",
            "^/ecom/",
        ]
        self.options = options or [0, 1, 14]
        self.ua_key = b"\x00\x01\x0e"
        self.character = (
            "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
        )
        self.character2 = (
            "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"
        )
        self.crypto_utility = _CryptoUtility(
            self.salt, [self.character, self.character2]
        )
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0"
        )
        self.browser_fp = fp or _BrowserFingerprintGenerator.generate_fingerprint(
            "Edge"
        )
        self.sort_index = [
            18,
            20,
            52,
            26,
            30,
            34,
            58,
            38,
            40,
            53,
            42,
            21,
            27,
            54,
            55,
            31,
            35,
            57,
            39,
            41,
            43,
            22,
            28,
            32,
            60,
            36,
            23,
            29,
            33,
            37,
            44,
            45,
            59,
            46,
            47,
            48,
            49,
            50,
            24,
            25,
            65,
            66,
            70,
            71,
        ]
        self.sort_index_2 = [
            18,
            20,
            26,
            30,
            34,
            38,
            40,
            42,
            21,
            27,
            31,
            35,
            39,
            41,
            43,
            22,
            28,
            32,
            36,
            23,
            29,
            33,
            37,
            44,
            45,
            46,
            47,
            48,
            49,
            50,
            24,
            25,
            52,
            53,
            54,
            55,
            57,
            58,
            59,
            60,
            65,
            66,
            70,
            71,
        ]

    def generate_abogus(self, params: str, body: str = "") -> tuple:
        ab_dir = {
            8: 3,
            15: {
                "aid": self.aid,
                "pageId": self.page_id,
                "boe": self.boe,
                "ddrt": self.ddrt,
                "paths": self.paths,
                "track": {"mode": 0, "delay": 300, "paths": []},
                "dump": True,
                "rpU": "",
            },
            18: 44,
            19: [1, 0, 1, 0, 1],
            66: 0,
            69: 0,
            70: 0,
            71: 0,
        }
        start_encryption = int(time.time() * 1000)
        array1 = self.crypto_utility.params_to_array(
            self.crypto_utility.params_to_array(params)
        )
        array2 = self.crypto_utility.params_to_array(
            self.crypto_utility.params_to_array(body)
        )
        array3 = self.crypto_utility.params_to_array(
            self.crypto_utility.base64_encode(
                _StringProcessor.to_char_str(
                    self.crypto_utility.rc4_encrypt(self.ua_key, self.user_agent)
                ),
                1,
            ),
            add_salt=False,
        )
        end_encryption = int(time.time() * 1000)

        ab_dir[20] = (start_encryption >> 24) & 255
        ab_dir[21] = (start_encryption >> 16) & 255
        ab_dir[22] = (start_encryption >> 8) & 255
        ab_dir[23] = start_encryption & 255
        ab_dir[24] = int(start_encryption / 256 / 256 / 256 / 256) >> 0
        ab_dir[25] = int(start_encryption / 256 / 256 / 256 / 256 / 256) >> 0
        ab_dir[26] = (self.options[0] >> 24) & 255
        ab_dir[27] = (self.options[0] >> 16) & 255
        ab_dir[28] = (self.options[0] >> 8) & 255
        ab_dir[29] = self.options[0] & 255
        ab_dir[30] = int(self.options[1] / 256) & 255
        ab_dir[31] = (self.options[1] % 256) & 255
        ab_dir[32] = (self.options[1] >> 24) & 255
        ab_dir[33] = (self.options[1] >> 16) & 255
        ab_dir[34] = (self.options[2] >> 24) & 255
        ab_dir[35] = (self.options[2] >> 16) & 255
        ab_dir[36] = (self.options[2] >> 8) & 255
        ab_dir[37] = self.options[2] & 255
        ab_dir[38] = array1[21]
        ab_dir[39] = array1[22]
        ab_dir[40] = array2[21]
        ab_dir[41] = array2[22]
        ab_dir[42] = array3[23]
        ab_dir[43] = array3[24]
        ab_dir[44] = (end_encryption >> 24) & 255
        ab_dir[45] = (end_encryption >> 16) & 255
        ab_dir[46] = (end_encryption >> 8) & 255
        ab_dir[47] = end_encryption & 255
        ab_dir[48] = ab_dir[8]
        ab_dir[49] = int(end_encryption / 256 / 256 / 256 / 256) >> 0
        ab_dir[50] = int(end_encryption / 256 / 256 / 256 / 256 / 256) >> 0
        ab_dir[51] = (self.page_id >> 24) & 255
        ab_dir[52] = (self.page_id >> 16) & 255
        ab_dir[53] = (self.page_id >> 8) & 255
        ab_dir[54] = self.page_id & 255
        ab_dir[55] = self.page_id
        ab_dir[56] = self.aid
        ab_dir[57] = self.aid & 255
        ab_dir[58] = (self.aid >> 8) & 255
        ab_dir[59] = (self.aid >> 16) & 255
        ab_dir[60] = (self.aid >> 24) & 255
        ab_dir[64] = len(self.browser_fp)
        ab_dir[65] = len(self.browser_fp)

        sorted_values = [ab_dir.get(i, 0) for i in self.sort_index]
        fp_array = _StringProcessor.to_char_array(self.browser_fp)
        ab_xor = (len(self.browser_fp) & 255) >> 8 & 255
        for index in range(len(self.sort_index_2) - 1):
            if index == 0:
                ab_xor = ab_dir.get(self.sort_index_2[index], 0)
            ab_xor ^= ab_dir.get(self.sort_index_2[index + 1], 0)
        sorted_values.extend(fp_array)
        sorted_values.append(ab_xor)

        abogus_bytes_str = (
            _StringProcessor.generate_random_bytes()
            + self.crypto_utility.transform_bytes(sorted_values)
        )
        abogus = self.crypto_utility.abogus_encode(abogus_bytes_str, 0)
        return (f"{params}&a_bogus={abogus}", abogus, self.user_agent, body)


def generate_abogus(
    params: str,
    body: str = "",
    user_agent: str = "",
    options: Optional[List[int]] = None,
    fp: str = "",
) -> str:
    """生成抖音 Web API 请求所需的 a_bogus 签名。

    Args:
        params: 原始查询字符串（如 ``device_platform=webapp&aid=6383&...``）
        body: 请求体字符串（GET 接口传空字符串）
        user_agent: 实际发送请求时使用的 User-Agent
        options: 签名选项，GET 默认 [0, 1, 14]（14 兼容 8）
        fp: 可选浏览器指纹，为空时随机生成

    Returns:
        a_bogus 签名字符串
    """
    return _ABogus(fp=fp, user_agent=user_agent, options=options).generate_abogus(
        params, body
    )[1]
