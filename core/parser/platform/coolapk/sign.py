"""酷安匿名客户端 V3 签名，保留上游 MIT 许可证与署名。"""

# 签名移植自 astrbot_plugin_coolapk_card（2026 leimi），算法与密码表源自
# coolapk-desktop（2026 daimiaopeng）；完整许可证见 resource/coolapk/。
# 来源：https://github.com/w13630039663-bit/astrbot_plugin_coolapk_card
# 来源：https://github.com/daimiaopeng/coolapk-desktop

from __future__ import annotations

import base64
import hashlib
import time
import uuid
from pathlib import Path

from ....logger import logger


APP_ID = "com.coolapk.market"
APP_CODE = 2604201

# 标准 base64 字母表：盐末位位移时使用
_STD_B64 = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
# bcrypt 字母表（./ 在前）：用于判断生成的盐是否可用
_BCRYPT_B64 = b"./ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"

USER_AGENT = (
    "Dalvik/2.1.0 (Linux; U; Android 16; 23113RKC6C Build/AQ3A.250226.002) "
    "(#Build; Redmi; 23113RKC6C; AQ3A.250226.002; 16) "
    "+CoolMarket/16.2.0-2604201-universal"
)

# 酷安 Token V3 密码表（base64，原文 1242 字符，来自上游项目 auth_blob.b64）
AUTH_BLOB_B64 = (
    "VFRCVU9GUXNRMEVsTFVNa1dERWpRU0VzVXlFbUxETkFVeTFEUEZZdUl5MGlNVU01SXpCVUpGY3RN"
    "eWtoTVVRMUpTMURPU1V4TXpoUUxWTXdWMDBzSXloU01UUXNXVEZEUkZFd1EwUlZNU1FrVVMwaktT"
    "TXRKRGhZTFZRa1VqRXpLU1FzTTBCVkxUUXdVaTAwTlNNc00yQlhMVk1sSkMxRE5GbE5MQ1EwV1RF"
    "ekxTUXhRMEVoTEVNbElqRXpLRmN3TXowbExpTkVWekVqUFNZdU5Ea2tMRk13Vml4VEtGa3RRemhY"
    "TEZNc1dDNGpMU1l0VTJCVFRUQTBMRkV1TXpoVExqTWhJakZETUZNc0kwQlZMQ1FrVUN3ekpTSXNJ"
    "ekJTTUVNOFVDeEVMU0l0STJCUUxVUW9VeTBqTEZNc0l5VWtNRlF3VmswdFJDeFlNVE1sSkM0ME5T"
    "RXdVMFJRTEZNaElpMGtOU013VTJCUUxqTmdVeXdqT0ZFc05DeFRMRU13V0N4VUxTRXRJeVJUTERO"
    "Qkl5MDBKRlpOTVVNc1VEQkVKRmd0TkMwa0xEUWtXQzFETUZNeFJDa2pMVk0wVml4VE1GZ3hJeTBt"
    "TFZNeEpTMHpKRlF4TTJCUUxTUTRXVEJETUZNdUkwVWhUU3d6UEZndFJEQlJNRE5FVVMwak9GWXVK"
    "RGtsTVNRMFV6QXpOU013UkRra01UTWtWU3hVTUZRdFJDeFlNVE5GSXl4RUxGWXNJelVoTGlRMFYw"
    "MHhOQ3hWTENNMFZ5NHpKRlV4TkN4VExGUTRWeTRqWUZZeEpEVWpMVU13VXpGRFBGSXNNMEVsTERN"
    "c1ZEQTBPU011TkNrbUxFUWtWaXhFTVNKTkxUTW9VaXd6TEZjdFUwUlFMaVF3VlRGREpTVXdVeWtq"
    "TVVNaEl5MHpJU1VzSTBFak1UTXdVUzFVTkZjeFJERWxMVVF0SlM0a05GSXNJemhaVFRCVFlGRXdR"
    "eVJZTFNNbElTNDBMRmdzVkRSWkxVUW9XVEJEUUZZdFJDeFdMak5FVkRCRE1Ga3RORFVsTEZRNFZT"
    "MVVKRmd3TXpSV0xEUTRVVTB3UXkwbUxVUTRXUzFUTVNNc1V5VWlNVU5BVVN4VUxGWXVJMFJVTGlN"
    "c1dERkVMRk14STJCUUxWTTBXUzRrT0Zjd00wUlhMVk1vV0MwaktGRXhMQ00wVml4VE5GQXRSRFJX"
    "TFVRbEpDMVRKRkF3TkN4Z1lB"
)

_BLOB = base64.b64decode(AUTH_BLOB_B64)

# 探测窗口：某些时间戳生成的盐不被 bcrypt 接受，按上游实现向前最多找 15 秒
_MAX_OFFSET = 16


class TokenError(RuntimeError):
    """签名不可用（通常缺 bcrypt）。"""


def md5_hex(raw: bytes) -> str:
    """计算签名所需的 MD5 十六进制文本。

    Args:
        raw: 参与签名计算的原始字节。

    Returns:
        MD5 十六进制文本。
    """
    return hashlib.md5(raw).hexdigest()


def _bcrypt_hash(password: bytes, salt22: str) -> bytes:
    """cost=4 的 bcrypt，输出 $2y$04$... 形式的 60 字节字符串。"""
    try:
        import bcrypt  # noqa: PLC0415  延迟导入，缺依赖时给出明确提示
    except ImportError as exc:  # pragma: no cover
        raise TokenError("缺少 bcrypt 依赖，无法生成酷安签名（pip install bcrypt）") from exc
    return bcrypt.hashpw(password, f"$2y$04${salt22}".encode())


def _shift_last(value: bytes, shift: int = -5) -> bytes:
    """把盐的最后一位在标准 base64 字母表上位移（上游算法要求）。"""
    prefix, last = value[:-1], value[-1:]
    index = _STD_B64.index(last)
    return prefix + bytes([_STD_B64[(index + shift) % 64]])


def _salt_is_usable(salt22: bytes) -> bool:
    """bcrypt 只接受末位落在特定子集的 22 字符盐，提前过滤可省去一次 hash。"""
    if any(byte not in _BCRYPT_B64 for byte in salt22):
        return False
    index = _BCRYPT_B64.index(salt22[-1:])
    return index % 4 == 0


def generate_token_at(device_code: str, timestamp: int) -> str:
    """按给定时间戳生成酷安 V3 签名。

    Args:
        device_code: 当前匿名设备码。
        timestamp: 签名使用的秒级时间戳。

    Returns:
        带有 v3 前缀的客户端签名。

    Raises:
        ValueError: 当前时间戳生成的盐不可用，调用方可换时间戳重试。
        TokenError: 缺少签名依赖。
    """
    index = ((timestamp + APP_CODE) % 100) * 4 + 0x80
    chunk = _BLOB[index:index + 0x80]
    segment = base64.b64decode(chunk)

    device_md5 = md5_hex(device_code.encode())
    plain = f"{APP_ID}&{segment.decode('utf-8', 'replace')}&{device_md5}&{timestamp}&{APP_CODE}"

    password = md5_hex(base64.b64encode(plain.encode())).encode()
    salt_source = base64.b64encode(
        f"{timestamp:x}/{md5_hex(plain.encode())}".encode()
    ).rstrip(b"=")
    salt22 = _shift_last(salt_source[:22])

    if not _salt_is_usable(salt22):
        raise ValueError("酷安签名盐不符合 bcrypt 格式")

    hashed = _bcrypt_hash(password, salt22.decode())
    return "v3" + base64.b64encode(hashed).decode().rstrip("=")


def get_app_token(device_code: str) -> str:
    """生成当前可用的客户端签名。

    Args:
        device_code: 当前匿名设备码。

    Returns:
        用于 X-App-Token 请求头的签名。

    Raises:
        TokenError: 缺少签名依赖或探测窗口内没有可用签名。
    """
    now = int(time.time())
    last_error: Exception | None = None
    for offset in range(_MAX_OFFSET):
        try:
            return generate_token_at(device_code, now + offset)
        except ValueError as exc:
            last_error = exc
    raise TokenError(f"连续 {_MAX_OFFSET} 个时间戳都生成不出可用签名: {last_error}")


def build_headers(device_code: str) -> dict[str, str]:
    """构建与官方客户端一致的酷安接口请求头。

    Args:
        device_code: 当前匿名设备码。

    Returns:
        包含本次签名和设备码的请求头。

    Raises:
        TokenError: 无法生成本次请求的签名。
    """
    headers = {
        "User-Agent": USER_AGENT,
        "X-App-Token": get_app_token(device_code),
        "X-Requested-With": "XMLHttpRequest",
        "X-Sdk-Int": "36",
        "X-Sdk-Locale": "zh-CN",
        "X-App-Mode": "universal",
        "X-App-Channel": "coolapk",
        "X-App-Id": APP_ID,
        "X-App-Version": "16.2.0",
        "X-App-Code": str(APP_CODE),
        "X-Api-Version": "16",
        "X-App-Supported": str(APP_CODE),
        "X-Dark-Mode": "0",
        "X-App-Device": device_code,
    }
    return headers


def load_or_create_device(storage_dir: str | Path) -> str:
    """读取持久化的匿名设备码，缺失或编码损坏时重新生成。

    Args:
        storage_dir: 配置管理器提供的酷安运行时目录。

    Returns:
        已保存的设备码；目录不可读写时沿用一次性设备码处理。
    """
    path = Path(storage_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        marker = path / "device_code.txt"
        if marker.exists():
            try:
                saved = marker.read_text(encoding="utf-8").strip()
            except UnicodeDecodeError:
                logger.warning("酷安设备码文件编码损坏，正在重新生成并保存")
                saved = ""
            if saved:
                return saved
        code = str(uuid.uuid4())
        marker.write_text(code, encoding="utf-8")
        return code
    except OSError as exc:
        logger.warning(f"酷安设备码无法读取或保存，本次使用临时设备码：{exc}")
        return str(uuid.uuid4())
