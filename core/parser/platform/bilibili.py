"""B 站解析器，实现视频/动态解析、鉴权与热评提取。"""
import asyncio
import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, parse_qs, urlencode

import aiohttp

from ...logger import logger

from .base import BaseVideoParser
from ..runtime_manager.bilibili.auth import BilibiliAuthRuntime
from ..utils import build_request_headers, is_live_url, SkipParse, format_duration_ms
from ...constants import Config

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
B23_HOST = "b23.tv"
BV_RE = re.compile(r"[Bb][Vv][0-9A-Za-z]{10,}")
AV_RE = re.compile(r"[Aa][Vv](\d+)")
EP_PATH_RE = re.compile(r"/bangumi/play/ep(\d+)", re.IGNORECASE)
EP_QS_RE = re.compile(r"(?:^|[?&])ep_id=(\d+)", re.IGNORECASE)
SS_PATH_RE = re.compile(r"/bangumi/play/ss(\d+)", re.IGNORECASE)
SS_QS_RE = re.compile(r"(?:^|[?&])season_id=(\d+)", re.IGNORECASE)
OPUS_RE = re.compile(r"/opus/(\d+)", re.IGNORECASE)
T_BILIBILI_RE = re.compile(r"t\.bilibili\.com/(\d+)", re.IGNORECASE)
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
XOR_CODE = 23442827791579
MAX_AID = 1 << 51
BASE = 58
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
HOT_COMMENT_API = "https://api.bilibili.com/x/v2/reply/wbi/main"
HOT_COMMENT_MODE = 3
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def av2bv(av: int) -> str:
    """将AV号转换为BV号

    参考:
        https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/bvid_desc.md

    Args:
        av: AV号（整数）

    Returns:
        BV号字符串
    """
    bytes_arr = [
        'B', 'V', '1', '0', '0', '0', '0', '0', '0', '0', '0', '0'
    ]
    bv_idx = len(bytes_arr) - 1
    tmp = (MAX_AID | av) ^ XOR_CODE
    while tmp > 0:
        bytes_arr[bv_idx] = BV_TABLE[tmp % BASE]
        tmp = tmp // BASE
        bv_idx -= 1
    bytes_arr[3], bytes_arr[9] = bytes_arr[9], bytes_arr[3]
    bytes_arr[4], bytes_arr[7] = bytes_arr[7], bytes_arr[4]
    return ''.join(bytes_arr)


class BilibiliParser(BaseVideoParser):

    """B 站解析器，支持视频/动态解析与热评提取。"""
    def __init__(
        self,
        cookie_runtime_enabled: bool = False,
        configured_cookie: str = "",
        admin_assist_enabled: bool = False,
        admin_reply_timeout_minutes: int = 1440,
        admin_request_cooldown_minutes: int = 1440,
        credential_path: str = "",
        local_debug_mode: bool = False,
        max_quality: int = 0,
        hot_comment_count: int = 0
    ):
        """初始化B站解析器"""
        super().__init__("bilibili")
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self.cookie_runtime_enabled = bool(cookie_runtime_enabled)
        try:
            self.max_qn = max(0, int(max_quality))
        except (TypeError, ValueError):
            self.max_qn = 0
        try:
            self.hot_comment_count = max(0, int(hot_comment_count))
        except (TypeError, ValueError):
            self.hot_comment_count = 0
        self.admin_assist_enabled = bool(admin_assist_enabled)
        self.admin_reply_timeout_minutes = max(1, int(admin_reply_timeout_minutes))
        self.admin_request_cooldown_minutes = max(
            1,
            int(admin_request_cooldown_minutes)
        )
        self.auth_runtime = BilibiliAuthRuntime(
            enabled=self.cookie_runtime_enabled,
            configured_cookie=configured_cookie,
            credential_path=credential_path,
            local_debug_mode=local_debug_mode
        )
        self._assist_request_reason: Optional[str] = None
        self._assist_request_pending = False
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com",
            "Accept-Encoding": "gzip, deflate",
        }

    def get_auth_runtime(self) -> BilibiliAuthRuntime:
        """返回当前解析器共享的 B 站鉴权运行时对象。"""
        return self.auth_runtime

    def consume_assist_request(self) -> Optional[str]:
        """读取并消费待处理的管理员辅助登录请求原因。"""
        if not self._assist_request_pending:
            return None
        self._assist_request_pending = False
        return self._assist_request_reason or "cookie_unavailable"

    def _mark_assist_request(self, reason: str) -> None:
        """记录需要触发管理员辅助登录的原因。"""
        if not self.cookie_runtime_enabled or not self.admin_assist_enabled:
            return
        self._assist_request_pending = True
        self._assist_request_reason = reason or "cookie_unavailable"

    async def _resolve_cookie_header(
        self,
        session: aiohttp.ClientSession
    ) -> str:
        """异步获取当前请求可用的 Cookie 请求头。"""
        if not self.cookie_runtime_enabled:
            return ""

        timeout_seconds = self.admin_reply_timeout_minutes * 60
        if self.auth_runtime.local_debug_mode:
            cookie_header = await self.auth_runtime.try_local_blocking_assist_once(
                session,
                timeout_seconds=timeout_seconds
            )
        else:
            cookie_header = await self.auth_runtime.get_cookie_header_for_request(
                session
            )
        if cookie_header:
            return cookie_header

        self._mark_assist_request(
            self.auth_runtime.cookie_unavailable_reason or "cookie_unavailable"
        )
        return ""

    def _build_api_headers(
        self,
        referer: Optional[str] = None,
        cookie_header: str = ""
    ) -> Dict[str, str]:
        """构建访问 B 站 API 所需请求头。"""
        headers = dict(self._default_headers)
        if referer:
            headers["Referer"] = referer
        if cookie_header:
            headers["Cookie"] = cookie_header
        return headers

    def _build_media_headers(
        self,
        referer: str,
        origin: str,
        cookie_header: str = ""
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """构建访问媒体资源所需请求头。"""
        custom_headers = {"Cookie": cookie_header} if cookie_header else None
        image_headers = build_request_headers(
            is_video=False,
            referer=referer,
            origin=origin,
            custom_headers=custom_headers
        )
        video_headers = build_request_headers(
            is_video=True,
            referer=referer,
            origin=origin,
            custom_headers=custom_headers
        )
        return image_headers, video_headers

    @staticmethod
    def _extract_key_from_url(url: str) -> str:
        """从 WBI 相关链接中提取密钥片段。"""
        path = urlparse(url).path
        return Path(path).stem

    @staticmethod
    def _get_mixin_key(img_key: str, sub_key: str) -> str:
        """按 WBI 规则混排密钥并生成 mixin_key。"""
        raw = img_key + sub_key
        return "".join(raw[i] for i in MIXIN_KEY_ENC_TAB)[:32]

    async def _get_wbi_mixin_key(
        self,
        session: aiohttp.ClientSession,
        headers: Dict[str, str]
    ) -> str:
        """异步拉取导航数据并计算 WBI mixin_key。"""
        request_headers = dict(headers)
        request_headers["Accept"] = "application/json, text/plain, */*"
        async with session.get(
            NAV_API,
            headers=request_headers,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            j = await self._check_json_response(resp)
        nav_data = j.get("data") or {}
        wbi_img = nav_data.get("wbi_img") or {}
        img_url = str(wbi_img.get("img_url", "")).strip()
        sub_url = str(wbi_img.get("sub_url", "")).strip()
        if not img_url or not sub_url:
            raise RuntimeError(
                "获取B站WBI签名密钥失败：缺少img_url/sub_url"
            )
        img_key = self._extract_key_from_url(img_url)
        sub_key = self._extract_key_from_url(sub_url)
        if not img_key or not sub_key:
            raise RuntimeError(
                "获取B站WBI签名密钥失败：img_key/sub_key为空"
            )
        return self._get_mixin_key(img_key, sub_key)

    @staticmethod
    def _sign_wbi_params(
        params: Dict[str, Any],
        mixin_key: str
    ) -> Dict[str, Any]:
        """为请求参数追加 WBI 签名字段。"""
        signed_params = dict(params)
        signed_params["wts"] = int(time.time())
        signed_params = dict(
            sorted(signed_params.items(), key=lambda item: item[0])
        )
        filtered_params = {}
        remove_chars = "!'()*"
        for key, value in signed_params.items():
            text = str(value)
            for ch in remove_chars:
                text = text.replace(ch, "")
            filtered_params[key] = text
        query = urlencode(filtered_params)
        w_rid = hashlib.md5(
            (query + mixin_key).encode("utf-8")
        ).hexdigest()
        filtered_params["w_rid"] = w_rid
        return filtered_params

    @staticmethod
    def _extract_initial_state_from_html(html: str) -> Dict[str, Any]:
        """从 HTML 中提取页面初始化状态 JSON。"""
        match = re.search(
            r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\});",
            html,
            re.DOTALL
        )
        if not match:
            match = re.search(
                r"window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>",
                html,
                re.DOTALL
            )
        if not match:
            return {}
        try:
            return json.loads(match.group(1))
        except Exception:
            return {}

    async def _resolve_opus_comment_subject(
        self,
        session: aiohttp.ClientSession,
        opus_url: str,
        headers: Dict[str, str]
    ) -> Optional[Tuple[int, int]]:
        """解析动态评论接口所需的 oid/type 参数。"""
        async with session.get(
            opus_url,
            headers=headers,
            allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            html = await resp.text()
        state = self._extract_initial_state_from_html(html)
        detail = state.get("detail") or {}
        basic = detail.get("basic") or {}
        comment_id_str = str(basic.get("comment_id_str") or "").strip()
        comment_type = basic.get("comment_type")
        if comment_id_str.isdigit() and isinstance(comment_type, int):
            return int(comment_id_str), int(comment_type)
        return None

    @staticmethod
    def _normalize_hot_comment_item(item: Dict[str, Any]) -> Dict[str, Any]:
        """将原始热评结构标准化为统一字段。"""
        member = item.get("member") or {}
        content = item.get("content") or {}
        ctime = item.get("ctime")
        if isinstance(ctime, int):
            time_text = datetime.fromtimestamp(ctime).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        else:
            time_text = ""
        try:
            likes = int(item.get("like", 0) or 0)
        except (TypeError, ValueError):
            likes = 0
        return {
            "username": str(member.get("uname", "") or ""),
            "uid": str(member.get("mid", "") or ""),
            "likes": likes,
            "message": str(content.get("message", "") or "").replace(
                "\n",
                " "
            ).strip(),
            "time": time_text,
        }

    async def _fetch_hot_comments(
        self,
        session: aiohttp.ClientSession,
        oid: int,
        comment_type: int,
        referer: str,
        cookie_header: str = ""
    ) -> List[Dict[str, Any]]:
        """异步请求并提取热评列表。"""
        if self.hot_comment_count <= 0:
            return []
        if not isinstance(oid, int) or oid <= 0:
            return []

        headers = self._build_api_headers(
            referer=referer,
            cookie_header=cookie_header
        )
        headers["Accept"] = "application/json, text/plain, */*"
        mixin_key = await self._get_wbi_mixin_key(session, headers)
        params = {
            "oid": oid,
            "type": comment_type,
            "mode": HOT_COMMENT_MODE,
            "next": 0,
            "plat": 1,
        }
        signed_params = self._sign_wbi_params(params, mixin_key)

        async with session.get(
            HOT_COMMENT_API,
            headers=headers,
            params=signed_params,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "hot comments")
        data_obj = j.get("data") or {}
        replies = data_obj.get("replies") or []
        top_replies = data_obj.get("top_replies") or []

        all_items: List[Dict[str, Any]] = []
        if isinstance(top_replies, list):
            all_items.extend(x for x in top_replies if isinstance(x, dict))
        if isinstance(replies, list):
            all_items.extend(x for x in replies if isinstance(x, dict))

        deduped_items: List[Dict[str, Any]] = []
        seen_rpid = set()
        for item in all_items:
            rpid = item.get("rpid")
            dedupe_key = rpid if rpid is not None else id(item)
            if dedupe_key in seen_rpid:
                continue
            seen_rpid.add(dedupe_key)
            deduped_items.append(item)

        comments = [
            self._normalize_hot_comment_item(item)
            for item in deduped_items
        ]
        comments = [item for item in comments if item.get("message")]
        comments.sort(key=lambda x: x.get("likes", 0), reverse=True)
        return comments[:self.hot_comment_count]

    async def _attach_hot_comments_to_result(
        self,
        session: aiohttp.ClientSession,
        result: Dict[str, Any],
        oid: Optional[int],
        comment_type: int,
        referer: str,
        cookie_header: str = ""
    ) -> None:
        """按配置将热评附加到解析结果中。"""
        if self.hot_comment_count <= 0:
            return
        if not isinstance(result, dict):
            return
        if oid is None:
            return
        try:
            oid_int = int(oid)
        except (TypeError, ValueError):
            return
        if oid_int <= 0:
            return
        try:
            comments = await self._fetch_hot_comments(
                session=session,
                oid=oid_int,
                comment_type=comment_type,
                referer=referer,
                cookie_header=cookie_header
            )
            if comments:
                result["hot_comments"] = comments
        except Exception as e:
            logger.warning(
                f"[{self.name}] 获取热评失败: oid={oid_int}, "
                f"type={comment_type}, 错误: {e}"
            )
    
    def _prepare_aid_param(self, aid: str) -> int:
        """将aid转换为整数

        Args:
            aid: AV号字符串或整数

        Returns:
            AV号整数，如果转换失败返回原值
        """
        try:
            return int(aid) if isinstance(aid, str) else aid
        except (ValueError, TypeError):
            return aid

    async def _check_json_response(
        self,
        resp: aiohttp.ClientResponse
    ) -> dict:
        """检查并解析JSON响应

        Args:
            resp: HTTP响应对象

        Returns:
            JSON响应字典

        Raises:
            RuntimeError: 响应不是JSON格式时
        """
        if resp.content_type != 'application/json':
            text = await resp.text()
            raise RuntimeError(
                f"API返回非JSON响应 "
                f"(状态码: {resp.status}, "
                f"Content-Type: {resp.content_type}): {text[:200]}"
            )
        return await resp.json()

    async def _handle_api_response(self, j: dict, api_name: str) -> None:
        """处理API响应，检查错误码

        Args:
            j: API响应JSON字典
            api_name: API名称

        Raises:
            RuntimeError: API返回错误码时
        """
        if j.get("code") != 0:
            error_msg = j.get('message', '未知错误')
            error_code = j.get('code')
            raise RuntimeError(
                f"{api_name} error: {error_code} {error_msg}"
            )

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL（支持视频和动态链接）

        Args:
            url: 视频或动态链接

        Returns:
            是否可以解析
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_lower = url.lower()
        if 'live.bilibili.com' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 跳过直播链接 {url}")
            return False
        if 'space.bilibili.com' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 跳过空间链接 {url}")
            return False

        if '/opus/' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 匹配动态链接 {url}")
            return True
        if 't.bilibili.com' in url_lower:
            logger.debug(f"[{self.name}] can_parse: 匹配动态链接 {url}")
            return True

        if B23_HOST in urlparse(url).netloc.lower():
            logger.debug(f"[{self.name}] can_parse: 匹配b23短链 {url}")
            return True

        if BV_RE.search(url):
            logger.debug(f"[{self.name}] can_parse: 匹配BV号 {url}")
            return True
        if AV_RE.search(url):
            logger.debug(f"[{self.name}] can_parse: 匹配AV号 {url}")
            return True
        if (
            EP_PATH_RE.search(url) or
            EP_QS_RE.search(url) or
            SS_PATH_RE.search(url) or
            SS_QS_RE.search(url)
        ):
            logger.debug(f"[{self.name}] can_parse: 匹配番剧链接 {url}")
            return True
        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取B站链接，最大程度兼容各种格式

        Args:
            text: 输入文本

        Returns:
            B站链接列表
        """
        result_links_set = set()
        seen_ids = set()
        
        b23_pattern = r'https?://[Bb]23\.tv/[^\s<>"\'()]+'
        b23_links = re.findall(b23_pattern, text, re.IGNORECASE)
        result_links_set.update(b23_links)
        
        bilibili_domains = r'(?:www|m|mobile)\.bilibili\.com'
        
        bv_url_pattern = (
            rf'https?://{bilibili_domains}/video/'
            rf'([Bb][Vv][0-9A-Za-z]{{10,}})[^\s<>"\'()]*'
        )
        bv_url_matches = re.finditer(bv_url_pattern, text, re.IGNORECASE)
        for match in bv_url_matches:
            bvid = match.group(1)
            if bvid[0:2].upper() != "BV":
                bvid = "BV" + bvid[2:]
            bvid_key = f"BV:{bvid}"
            if bvid_key not in seen_ids:
                seen_ids.add(bvid_key)
                normalized_url = f"https://www.bilibili.com/video/{bvid}"
                result_links_set.add(normalized_url)
        
        av_url_pattern = (
            rf'https?://{bilibili_domains}/video/'
            rf'[Aa][Vv](\d+)[^\s<>"\'()]*'
        )
        av_url_matches = re.finditer(av_url_pattern, text, re.IGNORECASE)
        for match in av_url_matches:
            av_num = match.group(1)
            av_key = f"AV:{av_num}"
            if av_key not in seen_ids:
                seen_ids.add(av_key)
                av_url = f"https://www.bilibili.com/video/av{av_num}"
                result_links_set.add(av_url)
        
        ep_url_pattern = (
            rf'https?://{bilibili_domains}/bangumi/play/'
            rf'ep(\d+)[^\s<>"\'()]*'
        )
        ep_url_matches = re.finditer(ep_url_pattern, text, re.IGNORECASE)
        for match in ep_url_matches:
            ep_id = match.group(1)
            ep_key = f"EP:{ep_id}"
            if ep_key not in seen_ids:
                seen_ids.add(ep_key)
                ep_url = f"https://www.bilibili.com/bangumi/play/ep{ep_id}"
                result_links_set.add(ep_url)

        ss_url_pattern = (
            rf'https?://{bilibili_domains}/bangumi/play/'
            rf'ss(\d+)[^\s<>"\'()]*'
        )
        ss_url_matches = re.finditer(ss_url_pattern, text, re.IGNORECASE)
        for match in ss_url_matches:
            season_id = match.group(1)
            ss_key = f"SS:{season_id}"
            if ss_key not in seen_ids:
                seen_ids.add(ss_key)
                ss_url = f"https://www.bilibili.com/bangumi/play/ss{season_id}"
                result_links_set.add(ss_url)
        
        bv_standalone_pattern = r'\b[Bb][Vv][0-9A-Za-z]{10,}\b'
        bv_standalone_matches = re.finditer(
            bv_standalone_pattern,
            text,
            re.IGNORECASE
        )
        for match in bv_standalone_matches:
            bvid = match.group(0)
            if bvid[0:2].upper() != "BV":
                bvid = "BV" + bvid[2:]
            bvid_key = f"BV:{bvid}"
            if bvid_key not in seen_ids:
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                if ('http://' not in context.lower() and
                        'https://' not in context.lower()):
                    seen_ids.add(bvid_key)
                    bv_url = f"https://www.bilibili.com/video/{bvid}"
                    result_links_set.add(bv_url)
        
        av_standalone_pattern = r'\b[Aa][Vv](\d+)\b'
        av_standalone_matches = re.finditer(
            av_standalone_pattern,
            text,
            re.IGNORECASE
        )
        for match in av_standalone_matches:
            av_num = match.group(1)
            av_key = f"AV:{av_num}"
            if av_key not in seen_ids:
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                if ('http://' not in context.lower() and
                        'https://' not in context.lower()):
                    seen_ids.add(av_key)
                    av_url = f"https://www.bilibili.com/video/av{av_num}"
                    result_links_set.add(av_url)

        opus_pattern = (
            rf'https?://(?:www|m|mobile)\.bilibili\.com/opus/'
            rf'(\d+)[^\s<>"\'()]*'
        )
        opus_matches = re.finditer(opus_pattern, text, re.IGNORECASE)
        for match in opus_matches:
            opus_id = match.group(1)
            opus_key = f"OPUS:{opus_id}"
            if opus_key not in seen_ids:
                seen_ids.add(opus_key)
                opus_url = f"https://www.bilibili.com/opus/{opus_id}"
                result_links_set.add(opus_url)

        t_bilibili_pattern = (
            r'https?://t\.bilibili\.com/'
            r'(\d+)[^\s<>"\'()]*'
        )
        t_bilibili_matches = re.finditer(t_bilibili_pattern, text, re.IGNORECASE)
        for match in t_bilibili_matches:
            dynamic_id = match.group(1)
            dynamic_key = f"T:{dynamic_id}"
            if dynamic_key not in seen_ids:
                seen_ids.add(dynamic_key)
                t_bilibili_url = f"https://t.bilibili.com/{dynamic_id}"
                result_links_set.add(t_bilibili_url)

        result = list(result_links_set)
        if result:
            logger.debug(f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: {result[:3]}{'...' if len(result) > 3 else ''}")
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result

    async def expand_b23(
        self,
        url: str,
        session: aiohttp.ClientSession
    ) -> str:
        """展开b23短链

        Args:
            url: 原始URL
            session: aiohttp会话

        Returns:
            展开后的URL，如果展开失败返回原URL
        """
        if urlparse(url).netloc.lower() == B23_HOST:
            headers = {
                "User-Agent": UA,
                "Referer": "https://www.bilibili.com",
                "Accept-Encoding": "gzip, deflate",
            }
            try:
                async with session.get(
                    url,
                    headers=headers,
                    allow_redirects=True,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as r:
                    expanded_url = str(r.url)
                    return expanded_url
            except Exception:
                return url
        return url

    def extract_p(self, url: str) -> int:
        """提取分P序号

        Args:
            url: 视频URL

        Returns:
            分P序号，默认为1
        """
        try:
            return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
        except Exception:
            return 1

    def extract_opus_id(self, url: str) -> Optional[str]:
        """从URL中提取动态ID（支持opus和t.bilibili.com格式）

        Args:
            url: 动态链接

        Returns:
            动态ID，提取失败时为None
        """
        match = T_BILIBILI_RE.search(url)
        if match:
            return match.group(1)

        match = OPUS_RE.search(url)
        if match:
            return match.group(1)
        return None

    async def get_opus_info(
        self,
        opus_id: str,
        session: aiohttp.ClientSession,
        referer: str = None,
        cookie_header: str = ""
    ) -> Dict[str, Any]:
        """获取opus动态信息

        Args:
            opus_id: opus ID（动态ID）
            session: aiohttp会话
            referer: 引用页面URL

        Returns:
            动态信息字典

        Raises:
            RuntimeError: API返回错误时
        """
        api = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail"
        params = {"dynamic_id": opus_id}
        headers = self._build_api_headers(
            referer=referer or f"https://www.bilibili.com/opus/{opus_id}",
            cookie_header=cookie_header
        )

        async with session.get(
            api,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "opus detail")
        return j.get("data", {})

    def _extract_video_url_from_data(self, data: dict) -> Optional[str]:
        """从数据中提取视频链接

        Args:
            data: 包含视频信息的字典

        Returns:
            视频链接，提取失败时为None
        """
        if not isinstance(data, dict):
            return None

        bvid = data.get("bvid")
        aid = data.get("aid")

        if bvid:
            return f"https://www.bilibili.com/video/{bvid}"
        elif aid:
            try:
                aid_int = int(aid)
                bvid_converted = av2bv(aid_int)
                return f"https://www.bilibili.com/video/{bvid_converted}"
            except (ValueError, TypeError, OverflowError):
                return f"https://www.bilibili.com/video/av{aid}"

        return None

    def detect_target(
        self,
        url: str
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """检测视频类型和标识符（支持视频和番剧）

        Args:
            url: 视频URL

        Returns:
            包含视频类型和标识符字典的元组
            (视频类型: "ugc"或"pgc", 标识符字典)
        """
        m = EP_PATH_RE.search(url) or EP_QS_RE.search(url)
        if m:
            return "pgc", {"ep_id": m.group(1)}
        m = SS_PATH_RE.search(url) or SS_QS_RE.search(url)
        if m:
            return "pgc", {"season_id": m.group(1)}
        m = BV_RE.search(url)
        if m:
            bvid = m.group(0)
            if bvid[0:2].upper() != "BV":
                bvid = "BV" + bvid[2:]
            return "ugc", {"bvid": bvid}
        m = AV_RE.search(url)
        if m:
            try:
                aid = int(m.group(1))
                bvid = av2bv(aid)
                return "ugc", {"bvid": bvid}
            except (ValueError, OverflowError):
                return "ugc", {"aid": m.group(1)}
        return None, {}

    async def get_ugc_info(
        self,
        bvid: str = None,
        aid: str = None,
        session: aiohttp.ClientSession = None,
        cookie_header: str = ""
    ) -> Dict[str, str]:
        """获取UGC视频信息

        Args:
            bvid: BV号
            aid: AV号
            session: aiohttp会话

        Returns:
            包含title、desc、author的字典

        Raises:
            ValueError: bvid和aid都未提供时
            RuntimeError: 当API返回错误时
        """
        api = "https://api.bilibili.com/x/web-interface/view"
        params = {}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        async with session.get(
            api,
            params=params,
            headers=self._build_api_headers(cookie_header=cookie_header),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "view")
        data = j["data"]
        title = data.get("title") or ""
        desc = data.get("desc") or ""
        owner = data.get("owner") or {}
        name = owner.get("name") or ""
        mid = owner.get("mid")
        if name and mid:
            author = f"{name}(uid:{mid})"
        elif name:
            author = name
        elif mid:
            author = f"(uid:{mid})"
        else:
            author = ""
        
        timestamp = ""
        pubdate = data.get("pubdate")
        if pubdate:
            dt = datetime.fromtimestamp(int(pubdate))
            timestamp = dt.strftime("%Y-%m-%d")
        
        rights = data.get("rights") or {}
        aid_value = data.get("aid")
        try:
            aid_value = int(aid_value) if aid_value is not None else None
        except (TypeError, ValueError):
            aid_value = None
        return {
            "title": title,
            "desc": desc,
            "author": author,
            "timestamp": timestamp,
            "aid": aid_value,
            "content_access_type_hint": (
                "charge_exclusive" if data.get("is_upower_exclusive")
                else "paid_exclusive" if any(
                    rights.get(key) for key in ("pay", "arc_pay", "ugc_pay")
                )
                else ""
            ),
            "is_upower_exclusive": bool(data.get("is_upower_exclusive")),
        }

    async def get_pgc_info_by_ep(
        self,
        ep_id: str,
        session: aiohttp.ClientSession,
        cookie_header: str = ""
    ) -> Dict[str, str]:
        """获取PGC视频信息

        Args:
            ep_id: 番剧集ID
            session: aiohttp会话

        Returns:
            包含title、desc、author的字典

        Raises:
            RuntimeError: API返回错误时
        """
        api = "https://api.bilibili.com/pgc/view/web/season"
        async with session.get(
            api,
            params={"ep_id": ep_id},
            headers=self._build_api_headers(cookie_header=cookie_header),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pgc season view")
        result = j.get("result") or j.get("data") or {}
        episodes = result.get("episodes") or []
        ep_obj = None
        for e in episodes:
            if str(e.get("ep_id")) == str(ep_id):
                ep_obj = e
                break
        title = ""
        if ep_obj:
            title = (
                ep_obj.get("share_copy") or
                ep_obj.get("long_title") or
                ep_obj.get("title") or ""
            )
        if not title:
            title = result.get("season_title") or result.get("title") or ""
        desc = result.get("evaluate") or result.get("summary") or ""
        name, mid = "", None
        up_info = result.get("up_info") or result.get("upInfo") or {}
        if isinstance(up_info, dict):
            name = up_info.get("name") or ""
            mid = up_info.get("mid") or up_info.get("uid")
        if not name:
            pub = result.get("publisher") or {}
            name = pub.get("name") or ""
            mid = pub.get("mid") or mid
        if name and mid:
            author = f"{name}(uid:{mid})"
        elif name:
            author = name
        elif mid:
            author = f"(uid:{mid})"
        else:
            author = result.get("season_title") or result.get("title") or ""
        
        timestamp = ""
        if ep_obj:
            pub_time = ep_obj.get("pub_time")
            if pub_time:
                dt = datetime.fromtimestamp(int(pub_time))
                timestamp = dt.strftime("%Y-%m-%d")

        aid_value = None
        if isinstance(ep_obj, dict):
            aid_value = ep_obj.get("aid")
        if aid_value is None:
            aid_value = result.get("aid")
        try:
            aid_value = int(aid_value) if aid_value is not None else None
        except (TypeError, ValueError):
            aid_value = None

        return {
            "title": title,
            "desc": desc,
            "author": author,
            "timestamp": timestamp,
            "aid": aid_value,
        }

    async def get_first_ep_id_by_season(
        self,
        season_id: str,
        session: aiohttp.ClientSession,
        cookie_header: str = ""
    ) -> str:
        """根据season_id解析首个可用ep_id。"""
        api = "https://api.bilibili.com/pgc/view/web/season"
        async with session.get(
            api,
            params={"season_id": season_id},
            headers=self._build_api_headers(cookie_header=cookie_header),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pgc season view by season_id")
        result = j.get("result") or j.get("data") or {}
        episodes = result.get("episodes") or []
        if not episodes:
            raise RuntimeError(f"未找到番剧分集: season_id={season_id}")

        for episode in episodes:
            ep_id = episode.get("ep_id")
            if ep_id is not None:
                return str(ep_id)
        raise RuntimeError(f"season数据缺少ep_id: season_id={season_id}")

    async def get_pagelist(
        self,
        bvid: str = None,
        aid: str = None,
        session: aiohttp.ClientSession = None,
        cookie_header: str = ""
    ):
        """获取分P列表

        Args:
            bvid: BV号
            aid: AV号
            session: aiohttp会话

        Returns:
            分P列表数据

        Raises:
            ValueError: bvid和aid都未提供时
            RuntimeError: 当API返回错误时
        """
        api = "https://api.bilibili.com/x/player/pagelist"
        params = {"jsonp": "json"}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        async with session.get(
            api,
            params=params,
            headers=self._build_api_headers(cookie_header=cookie_header),
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pagelist")
        return j["data"]

    async def ugc_playurl(
        self,
        bvid: str = None,
        aid: str = None,
        cid: int = None,
        qn: int = None,
        fnval: int = None,
        referer: str = None,
        session: aiohttp.ClientSession = None,
        cookie_header: str = ""
    ):
        """获取UGC视频播放地址（优先使用BV号，aid作为备用）

        Args:
            bvid: BV号
            aid: AV号
            cid: 分P的cid
            qn: 画质
            fnval: 视频流格式
            referer: 引用页面URL
            session: aiohttp会话

        Returns:
            播放地址数据

        Raises:
            ValueError: bvid和aid都未提供时
            RuntimeError: 当API返回错误时
        """
        api = "https://api.bilibili.com/x/player/playurl"
        params = {
            "cid": cid,
            "qn": qn,
            "fnver": 0,
            "fnval": fnval,
            "fourk": 1,
            "otype": "json",
            "platform": "html5",
            "high_quality": 1
        }
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        headers = self._build_api_headers(
            referer=referer,
            cookie_header=cookie_header
        )
        async with session.get(
            api,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "playurl")
        return j["data"]

    async def pgc_playurl_v2(
        self,
        ep_id: str,
        qn: int,
        fnval: int,
        referer: str,
        session: aiohttp.ClientSession,
        cookie_header: str = ""
    ):
        """获取PGC视频播放地址

        Args:
            ep_id: 番剧集ID
            qn: 画质
            fnval: 视频流格式
            referer: 引用页面URL
            session: aiohttp会话

        Returns:
            播放地址数据

        Raises:
            RuntimeError: API返回错误时
        """
        api = "https://api.bilibili.com/pgc/player/web/v2/playurl"
        params = {
            "ep_id": ep_id,
            "qn": qn,
            "fnver": 0,
            "fnval": fnval,
            "fourk": 1,
            "otype": "json"
        }
        headers = self._build_api_headers(
            referer=referer,
            cookie_header=cookie_header
        )
        async with session.get(
            api,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pgc playurl v2")
        return j.get("result") or j.get("data") or j

    def best_qn_from_data(self, data: Dict[str, Any]) -> Optional[int]:
        """从数据中获取最佳画质

        Args:
            data: 播放地址数据

        Returns:
            最佳画质代码，无法获取时为None
        """
        data = self._unwrap_playurl_data(data)
        aq = data.get("accept_quality") or []
        if isinstance(aq, list) and aq:
            try:
                candidates = [int(x) for x in aq]
                if self.max_qn > 0:
                    candidates = [qn for qn in candidates if qn <= self.max_qn]
                if candidates:
                    return max(candidates)
            except Exception:
                pass
        dash = data.get("dash") or {}
        if dash.get("video"):
            try:
                candidates = [int(v.get("id", 0)) for v in dash["video"]]
                if self.max_qn > 0:
                    candidates = [qn for qn in candidates if qn <= self.max_qn]
                if candidates:
                    return max(candidates)
            except Exception:
                pass
        return None

    def pick_best_video(self, dash_obj: Dict[str, Any]):
        """选择最佳视频流

        Args:
            dash_obj: DASH格式视频数据

        Returns:
            最佳视频流数据，未找到时为None
        """
        vids = dash_obj.get("video") or []
        if not vids:
            return None
        if self.max_qn > 0:
            limited_vids = []
            for video in vids:
                try:
                    if int(video.get("id", 0)) <= self.max_qn:
                        limited_vids.append(video)
                except (TypeError, ValueError):
                    continue
            if limited_vids:
                vids = limited_vids
        return sorted(
            vids,
            key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)),
            reverse=True
        )[0]

    def pick_best_audio(self, dash_obj: Dict[str, Any]):
        """选择最佳音频流。"""
        audios = dash_obj.get("audio") or []
        if not audios:
            return None
        return sorted(
            audios,
            key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)),
            reverse=True
        )[0]

    def _build_dash_download_url(self, dash_obj: Dict[str, Any]) -> Optional[str]:
        """从 DASH 数据中构建下载 URL（优先 video+audio）。"""
        best_video = self.pick_best_video(dash_obj)
        if not best_video:
            return None

        video_url = best_video.get("baseUrl") or best_video.get("base_url")
        if not video_url:
            return None

        best_audio = self.pick_best_audio(dash_obj)
        audio_url = (
            (best_audio.get("baseUrl") or best_audio.get("base_url"))
            if best_audio else
            ""
        )
        if audio_url:
            return f"dash:{video_url}||{audio_url}"
        return video_url

    @staticmethod
    def _unwrap_playurl_data(data: Dict[str, Any]) -> Dict[str, Any]:
        """兼容PGC接口将播放数据包裹在video_info中的情况。"""
        if not isinstance(data, dict):
            return {}
        video_info = data.get("video_info")
        if isinstance(video_info, dict) and video_info:
            return video_info
        return data

    @staticmethod
    def _sum_durl_length(durl_list: List[Dict[str, Any]]) -> Optional[int]:
        """累计 durl 分段时长并返回毫秒值。"""
        total = 0
        found = False
        for item in durl_list or []:
            if not isinstance(item, dict):
                continue
            length = item.get("length")
            try:
                total += int(length)
                found = True
            except (TypeError, ValueError):
                continue
        return total if found else None

    def _extract_available_length_ms(self, payload: Dict[str, Any]) -> Optional[int]:
        """提取当前可用播放时长（毫秒）。"""
        durl_length = self._sum_durl_length(payload.get("durl") or [])
        if durl_length is not None:
            return durl_length

        current_quality = payload.get("quality")
        for item in payload.get("durls") or []:
            if not isinstance(item, dict):
                continue
            if current_quality is not None and item.get("quality") != current_quality:
                continue
            nested_length = self._sum_durl_length(item.get("durl") or [])
            if nested_length is not None:
                return nested_length

        durls = payload.get("durls") or []
        if durls and isinstance(durls[0], dict):
            return self._sum_durl_length(durls[0].get("durl") or [])
        return None

    def _resolve_restriction_hint(
        self,
        access_info: Dict[str, Any],
        content_meta: Optional[Dict[str, Any]] = None,
        cookie_header: str = ""
    ) -> Tuple[str, str]:
        """根据响应内容推断访问受限原因。"""
        hint = ""
        if isinstance(content_meta, dict):
            hint = content_meta.get("content_access_type_hint", "") or ""

        restriction_type = ""
        if hint == "charge_exclusive":
            restriction_type = "charge_exclusive"
        elif hint == "paid_exclusive":
            restriction_type = "paid_exclusive"
        elif access_info.get("need_vip"):
            restriction_type = "vip_exclusive"
        elif access_info.get("has_paid") is False:
            restriction_type = "paid_exclusive"
        elif access_info.get("need_login") and not cookie_header:
            restriction_type = "login_required"

        restriction_label = {
            "charge_exclusive": "充电专属",
            "vip_exclusive": "大会员专享",
            "paid_exclusive": "付费专享",
            "login_required": "登录后可看",
        }.get(restriction_type, "")
        return restriction_type, restriction_label

    def _build_access_message(self, access_info: Dict[str, Any]) -> str:
        """将访问分析结果格式化为可读提示。"""
        status = access_info.get("status")
        restriction_label = access_info.get("restriction_label", "")

        if status == "full":
            return "当前链接可解析完整视频"

        available = format_duration_ms(access_info.get("available_length_ms"))
        full = format_duration_ms(access_info.get("timelength_ms"))
        if available and full:
            duration_text = f"{available} / {full}"
        elif available:
            duration_text = f"{available} / 未知全长"
        elif full:
            duration_text = f"未知可解析时长 / {full}"
        else:
            duration_text = "时长未知 / 时长未知"

        target_text = f"{restriction_label}视频" if restriction_label else "完整视频"

        if status == "preview_only":
            if restriction_label:
                return (
                    f"当前链接（{restriction_label}）无法获取完整视频，"
                    f"仅可解析试看片段（{duration_text}）"
                )
            return f"当前链接无法获取完整视频，仅可解析试看片段（{duration_text}）"

        detail_parts = []
        error_code = access_info.get("error_code")
        if error_code not in (None, 0):
            detail_parts.append(f"error_code={error_code}")
        raw_message = access_info.get("raw_message")
        if raw_message and raw_message not in ("0", "Success"):
            detail_parts.append(str(raw_message))
        detail_text = f"（{'，'.join(detail_parts)}）" if detail_parts else ""

        if status == "restricted":
            return f"当前链接无法解析{target_text}{detail_text}"
        if restriction_label:
            return f"当前链接暂时无法获取{restriction_label}可解析视频流{detail_text}"
        return f"当前链接暂时无法获取可解析视频流{detail_text}"

    def _analyze_play_access(
        self,
        data: Optional[Dict[str, Any]] = None,
        error: Optional[Exception] = None,
        content_meta: Optional[Dict[str, Any]] = None,
        cookie_header: str = ""
    ) -> Dict[str, Any]:
        """分析播放接口响应并判断可访问性。"""
        if error is not None:
            access_info = {
                "status": "unavailable",
                "can_access_full_video": False,
                "is_preview_only": False,
                "has_stream": False,
                "need_login": False,
                "need_vip": False,
                "has_paid": None,
                "play_detail": None,
                "error_code": None,
                "raw_message": str(error),
                "timelength_ms": None,
                "available_length_ms": None,
            }
            restriction_type, restriction_label = self._resolve_restriction_hint(
                access_info,
                content_meta,
                cookie_header=cookie_header
            )
            access_info["restriction_type"] = restriction_type
            access_info["restriction_label"] = restriction_label
            access_info["message"] = f"当前链接暂时无法获取可解析视频流（{error}）"
            return access_info

        wrapper = data if isinstance(data, dict) else {}
        payload = self._unwrap_playurl_data(wrapper)
        support_formats = payload.get("support_formats") or []
        need_vip = any(
            isinstance(item, dict) and item.get("need_vip")
            for item in support_formats
        )
        need_login = any(
            isinstance(item, dict) and item.get("need_login")
            for item in support_formats
        )
        has_paid = payload.get("has_paid")
        play_detail = (wrapper.get("play_check") or {}).get("play_detail")
        error_code = payload.get("error_code")
        raw_message = payload.get("message") or wrapper.get("message") or ""
        timelength_ms = payload.get("timelength")
        available_length_ms = self._extract_available_length_ms(payload)
        has_dash = bool((payload.get("dash") or {}).get("video"))
        has_durl = bool(payload.get("durl") or payload.get("durls"))
        has_stream = has_dash or has_durl

        is_preview_only = bool(payload.get("is_preview")) or play_detail == "PLAY_PREVIEW"
        if (
            not is_preview_only and timelength_ms and available_length_ms and
            int(available_length_ms) < int(timelength_ms)
        ):
            is_preview_only = True

        if has_stream and is_preview_only:
            status = "preview_only"
            can_access_full_video = False
        elif has_stream:
            status = "full"
            can_access_full_video = True
        elif error_code not in (None, 0) or play_detail:
            status = "restricted"
            can_access_full_video = False
        else:
            status = "unavailable"
            can_access_full_video = False

        access_info = {
            "status": status,
            "can_access_full_video": can_access_full_video,
            "is_preview_only": is_preview_only,
            "has_stream": has_stream,
            "need_login": need_login,
            "need_vip": need_vip,
            "has_paid": has_paid,
            "play_detail": play_detail,
            "error_code": error_code,
            "raw_message": raw_message,
            "timelength_ms": timelength_ms,
            "available_length_ms": available_length_ms,
        }
        restriction_type, restriction_label = self._resolve_restriction_hint(
            access_info,
            content_meta,
            cookie_header=cookie_header
        )
        access_info["restriction_type"] = restriction_type
        access_info["restriction_label"] = restriction_label
        access_info["message"] = self._build_access_message(access_info)
        return access_info

    async def _analyze_target_access(
        self,
        vtype: str,
        referer: str,
        session: aiohttp.ClientSession,
        content_meta: Optional[Dict[str, Any]] = None,
        cookie_header: str = "",
        bvid: str = None,
        aid: str = None,
        cid: int = None,
        ep_id: str = None,
    ) -> Dict[str, Any]:
        """异步检测目标链接访问状态并返回分析结果。"""
        try:
            if vtype == "ugc":
                data = await self.ugc_playurl(
                    bvid=bvid,
                    aid=aid,
                    cid=cid,
                    qn=120,
                    fnval=4048,
                    referer=referer,
                    session=session,
                    cookie_header=cookie_header
                )
            else:
                data = await self.pgc_playurl_v2(
                    ep_id=ep_id,
                    qn=120,
                    fnval=4048,
                    referer=referer,
                    session=session,
                    cookie_header=cookie_header
                )
            return self._analyze_play_access(
                data=data,
                content_meta=content_meta,
                cookie_header=cookie_header
            )
        except Exception as e:
            return self._analyze_play_access(
                error=e,
                content_meta=content_meta,
                cookie_header=cookie_header
            )

    @staticmethod
    def _access_fields_from_info(access_info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """从访问分析信息中提取对外字段。"""
        if not isinstance(access_info, dict):
            return {}
        return {
            "access_status": access_info.get("status", ""),
            "restriction_type": access_info.get("restriction_type", ""),
            "restriction_label": access_info.get("restriction_label", ""),
            "can_access_full_video": access_info.get("can_access_full_video"),
            "is_preview_only": access_info.get("is_preview_only", False),
            "access_message": access_info.get("message", ""),
            "timelength_ms": access_info.get("timelength_ms"),
            "available_length_ms": access_info.get("available_length_ms"),
        }

    async def _get_ugc_direct_url(
        self,
        bvid: str = None,
        aid: str = None,
        cid: int = None,
        referer: str = None,
        session: aiohttp.ClientSession = None,
        cookie_header: str = ""
    ) -> Optional[str]:
        """获取UGC视频直链（统一处理bvid和aid）

        Args:
            bvid: BV号（优先）
            aid: AV号（备用）
            cid: 分P的cid
            referer: 引用页面URL
            session: aiohttp会话

        Returns:
            视频直链，失败时为None
        """
        FNVAL_MAX = 4048
        if bvid:
            probe = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=120,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        else:
            probe = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=120,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        target_qn = (
            self.best_qn_from_data(probe) or
            self._unwrap_playurl_data(probe).get("quality") or
            80
        )
        if bvid:
            merged_try = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=target_qn,
                fnval=0,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        else:
            merged_try = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=target_qn,
                fnval=0,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        merged_payload = self._unwrap_playurl_data(merged_try)
        if merged_payload.get("durl"):
            return merged_payload["durl"][0].get("url")
        if bvid:
            dash_try = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=target_qn,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        else:
            dash_try = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=target_qn,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session,
                cookie_header=cookie_header
            )
        dash_payload = self._unwrap_playurl_data(dash_try)
        return self._build_dash_download_url(dash_payload.get("dash") or {})

    async def parse_opus(
        self,
        url: str,
        session: aiohttp.ClientSession,
        cookie_header: str = "",
        enable_hot_comments: bool = True
    ) -> Optional[Dict[str, Any]]:
        """解析B站动态链接

        Args:
            url: B站动态链接
            session: aiohttp会话

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        original_url = url

        if B23_HOST in urlparse(url).netloc.lower():
            expanded_url = await self.expand_b23(url, session)

            if '/opus/' not in expanded_url.lower() and 't.bilibili.com' not in expanded_url.lower():
                raise RuntimeError(f"短链指向的不是动态链接: {url}")

            url = expanded_url

        opus_id = self.extract_opus_id(url)
        if not opus_id:
            raise RuntimeError(f"无法从URL中提取opus ID: {url}")
        comment_oid: Optional[int] = None
        comment_type = 17
        if enable_hot_comments and self.hot_comment_count > 0:
            try:
                subject = await self._resolve_opus_comment_subject(
                    session=session,
                    opus_url=url,
                    headers=self._build_api_headers(
                        referer=url,
                        cookie_header=cookie_header
                    )
                )
                if subject:
                    comment_oid, comment_type = subject
            except Exception as e:
                logger.debug(
                    f"[{self.name}] 解析动态评论主体失败: {url}, 错误: {e}"
                )
            if comment_oid is None:
                try:
                    comment_oid = int(opus_id)
                except (TypeError, ValueError):
                    comment_oid = None

        data = await self.get_opus_info(
            opus_id,
            session,
            referer=url,
            cookie_header=cookie_header
        )

        card_data = data.get("card", {})
        if not card_data:
            raise RuntimeError(f"API返回数据为空: {url}")

        if isinstance(card_data, str):
            try:
                card_obj = json.loads(card_data)
            except json.JSONDecodeError:
                raise RuntimeError(f"无法解析card数据: {url}")
        else:
            card_obj = card_data

        desc_obj = card_obj.get("desc", {})

        inner_card_data = card_obj.get("card", {})
        if isinstance(inner_card_data, str):
            try:
                inner_card = json.loads(inner_card_data)
            except json.JSONDecodeError:
                inner_card = {}
        else:
            inner_card = inner_card_data

        mid = None
        name = ""
        if isinstance(desc_obj, dict):
            user_profile = desc_obj.get("user_profile", {})
            if isinstance(user_profile, dict):
                user_info = user_profile.get("info", {})
                if isinstance(user_info, dict):
                    mid = user_info.get("uid")
                    name = user_info.get("uname", "")

        if name and mid:
            author = f"{name}(uid:{mid})"
        elif name:
            author = name
        elif mid:
            author = f"(uid:{mid})"
        else:
            author = ""

        timestamp = ""
        if isinstance(desc_obj, dict):
            ts = desc_obj.get("timestamp")
            if ts:
                try:
                    ts_int = int(ts)
                    dt = datetime.fromtimestamp(ts_int)
                    timestamp = dt.strftime("%Y-%m-%d")
                except (ValueError, TypeError, OSError):
                    timestamp = str(ts)

        item = inner_card.get("item", {}) if isinstance(inner_card, dict) else {}
        title = ""
        desc = ""

        if isinstance(item, dict):
            content = item.get("content", "")
            description = item.get("description", "")

            dynamic_text = content if content else description
            if dynamic_text:
                title = dynamic_text[:100] if dynamic_text else ""
                desc = dynamic_text

        if not title:
            title = f"动态 #{opus_id}"

        dynamic_type = desc_obj.get("type") if isinstance(desc_obj, dict) else None
        orig_type = desc_obj.get("orig_type") if isinstance(desc_obj, dict) else None

        video_url = None
        origin_data_for_timestamp = None

        if dynamic_type == 8:
            if isinstance(inner_card, dict):
                video_url = self._extract_video_url_from_data(inner_card)

        elif dynamic_type == 1 and orig_type == 8:
            if isinstance(inner_card, dict):
                origin_data = inner_card.get("origin")
                if origin_data:
                    if isinstance(origin_data, str):
                        try:
                            origin_data = json.loads(origin_data)
                        except json.JSONDecodeError:
                            origin_data = {}

                    if isinstance(origin_data, dict):
                        video_url = self._extract_video_url_from_data(origin_data)
                        origin_data_for_timestamp = origin_data

        if video_url:
            video_result = await self.parse_bilibili_minimal(
                video_url,
                session=session,
                cookie_header_override=cookie_header,
                enable_hot_comments=False
            )

            if not video_result:
                raise RuntimeError(f"视频解析器返回空结果: {video_url}")

            is_forward = (dynamic_type == 1 and orig_type == 8)

            if is_forward:
                origin_title = video_result.get("title", "")
                origin_author = video_result.get("author", "")
                origin_desc = video_result.get("desc", "")
                origin_url = video_result.get("url", video_url)

                origin_timestamp = ""
                if origin_data_for_timestamp and isinstance(origin_data_for_timestamp, dict):
                    pubdate = origin_data_for_timestamp.get("pubdate")
                    ctime = origin_data_for_timestamp.get("ctime")
                    ts_value = pubdate if pubdate else ctime

                    if ts_value:
                        try:
                            ts_int = int(ts_value)
                            dt = datetime.fromtimestamp(ts_int)
                            origin_timestamp = dt.strftime("%Y-%m-%d")
                        except (ValueError, TypeError, OSError):
                            origin_timestamp = str(ts_value)

                final_title = title
                if not final_title or final_title == f"动态 #{opus_id}":
                    final_title = ""
                if final_title and origin_title:
                    final_title = f"{final_title} ({origin_title})"
                elif origin_title:
                    final_title = origin_title
                elif not final_title:
                    final_title = f"动态 #{opus_id}"

                if author and origin_author:
                    final_author = f"{author} ({origin_author})"
                elif origin_author:
                    final_author = origin_author
                else:
                    final_author = author

                final_desc = desc
                if final_desc and origin_desc:
                    final_desc = f"{final_desc} ({origin_desc})"
                elif origin_desc:
                    final_desc = origin_desc
                elif not final_desc:
                    final_desc = ""

                if timestamp and origin_timestamp:
                    final_timestamp = f"{timestamp} ({origin_timestamp})"
                elif origin_timestamp:
                    final_timestamp = origin_timestamp
                else:
                    final_timestamp = timestamp

                dynamic_url = original_url if B23_HOST in urlparse(original_url).netloc.lower() else url
                if dynamic_url and origin_url and dynamic_url != origin_url:
                    final_url = f"{dynamic_url} ({origin_url})"
                else:
                    final_url = dynamic_url

                referer = url
                origin = "https://www.bilibili.com"
                image_headers, video_headers = self._build_media_headers(
                    referer=referer,
                    origin=origin,
                    cookie_header=cookie_header
                )
                result = {
                    "url": final_url,
                    "title": final_title,
                    "author": final_author,
                    "desc": final_desc,
                    "timestamp": final_timestamp,
                    "video_urls": self._add_range_prefix_to_video_urls(video_result.get("video_urls", [])),
                    "image_urls": video_result.get("image_urls", []),
                    "image_headers": image_headers,
                    "video_headers": video_headers,
                    "access_status": video_result.get("access_status", ""),
                    "restriction_type": video_result.get("restriction_type", ""),
                    "restriction_label": video_result.get("restriction_label", ""),
                    "can_access_full_video": video_result.get("can_access_full_video"),
                    "is_preview_only": video_result.get("is_preview_only", False),
                    "access_message": video_result.get("access_message", ""),
                    "timelength_ms": video_result.get("timelength_ms"),
                    "available_length_ms": video_result.get("available_length_ms"),
                }
                if enable_hot_comments:
                    await self._attach_hot_comments_to_result(
                        session=session,
                        result=result,
                        oid=comment_oid,
                        comment_type=comment_type,
                        referer=url,
                        cookie_header=cookie_header
                    )
                return result
            else:
                final_title = title
                if not final_title or final_title == f"动态 #{opus_id}":
                    video_title = video_result.get("title", "")
                    if video_title:
                        final_title = video_title

                final_desc = desc
                if not final_desc:
                    video_desc = video_result.get("desc", "")
                    if video_desc:
                        final_desc = video_desc

                referer = url
                origin = "https://www.bilibili.com"
                image_headers, video_headers = self._build_media_headers(
                    referer=referer,
                    origin=origin,
                    cookie_header=cookie_header
                )
                result = {
                    "url": original_url if B23_HOST in urlparse(original_url).netloc.lower() else url,
                    "title": final_title,
                    "author": author,
                    "desc": final_desc,
                    "timestamp": timestamp,
                    "video_urls": self._add_range_prefix_to_video_urls(video_result.get("video_urls", [])),
                    "image_urls": video_result.get("image_urls", []),
                    "image_headers": image_headers,
                    "video_headers": video_headers,
                    "access_status": video_result.get("access_status", ""),
                    "restriction_type": video_result.get("restriction_type", ""),
                    "restriction_label": video_result.get("restriction_label", ""),
                    "can_access_full_video": video_result.get("can_access_full_video"),
                    "is_preview_only": video_result.get("is_preview_only", False),
                    "access_message": video_result.get("access_message", ""),
                    "timelength_ms": video_result.get("timelength_ms"),
                    "available_length_ms": video_result.get("available_length_ms"),
                }
                if enable_hot_comments:
                    await self._attach_hot_comments_to_result(
                        session=session,
                        result=result,
                        oid=comment_oid,
                        comment_type=comment_type,
                        referer=url,
                        cookie_header=cookie_header
                    )
                return result

        image_urls = []
        if isinstance(item, dict):
            pictures = item.get("pictures", [])
            if isinstance(pictures, list):
                for pic in pictures:
                    if isinstance(pic, dict):
                        pic_url = pic.get("img_src") or pic.get("imgSrc") or pic.get("url")
                        if pic_url:
                            image_urls.append([pic_url])
                    elif isinstance(pic, str):
                        image_urls.append([pic])

        display_url = original_url if B23_HOST in urlparse(original_url).netloc.lower() else url

        referer = url
        origin = "https://www.bilibili.com"
        image_headers, video_headers = self._build_media_headers(
            referer=referer,
            origin=origin,
            cookie_header=cookie_header
        )

        result = {
            "url": display_url,
            "title": title,
            "author": author,
            "desc": desc,
            "timestamp": timestamp,
            "video_urls": [],
            "image_urls": image_urls,
            "image_headers": image_headers,
            "video_headers": video_headers,
        }
        if enable_hot_comments:
            await self._attach_hot_comments_to_result(
                session=session,
                result=result,
                oid=comment_oid,
                comment_type=comment_type,
                referer=url,
                cookie_header=cookie_header
            )
        return result

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个B站链接

        Args:
            session: aiohttp会话
            url: B站链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            try:
                result = await self.parse_bilibili_minimal(url, session=session)
                if result:
                    logger.debug(
                        f"[{self.name}] parse: 解析成功 {url}, "
                        f"title={result.get('title', '')[:50]}, "
                        f"video_count={len(result.get('video_urls', []))}, "
                        f"image_count={len(result.get('image_urls', []))}"
                    )
                else:
                    logger.debug(f"[{self.name}] parse: 解析返回空结果 {url}")
                return result
            except Exception as e:
                logger.debug(f"[{self.name}] parse: 解析失败 {url}, 错误: {e}")
                raise

    async def parse_bilibili_minimal(
        self,
        url: str,
        p: Optional[int] = None,
        session: aiohttp.ClientSession = None,
        cookie_header_override: Optional[str] = None,
        enable_hot_comments: bool = True
    ) -> Optional[Dict[str, Any]]:
        """解析B站链接，返回视频或动态信息

        Args:
            url: B站链接
            p: 分P序号（可选）
            session: aiohttp会话（可选）

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        if session is None:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(
                headers={"User-Agent": UA},
                timeout=timeout
            ) as sess:
                return await self.parse_bilibili_minimal(
                    url,
                    p,
                    sess,
                    cookie_header_override=cookie_header_override,
                    enable_hot_comments=enable_hot_comments
                )
        logger.debug(f"[{self.name}] parse_bilibili_minimal: 开始处理 {url}")
        original_url = url
        page_url = await self.expand_b23(url, session)
        if page_url != url:
            logger.debug(f"[{self.name}] parse_bilibili_minimal: b23短链展开 {url} -> {page_url}")

        if cookie_header_override is not None:
            cookie_header = cookie_header_override
        else:
            cookie_header = await self._resolve_cookie_header(session)

        if is_live_url(page_url) or is_live_url(original_url):
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 检测到直播域名链接，跳过解析 {original_url} -> {page_url}")
            raise SkipParse("直播域名链接不解析")

        page_url_lower = page_url.lower()
        if '/opus/' in page_url_lower or 't.bilibili.com' in page_url_lower:
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 检测到动态链接，使用动态解析器")
            return await self.parse_opus(
                page_url,
                session,
                cookie_header=cookie_header,
                enable_hot_comments=enable_hot_comments
            )

        if not self.can_parse(page_url):
            raise RuntimeError(f"无法解析此URL: {url}")
        p_index = max(1, int(p or self.extract_p(page_url)))
        vtype, ident = self.detect_target(page_url)
        if not vtype:
            raise RuntimeError(f"无法识别视频类型: {url}")
        access_info: Dict[str, Any] = {}
        comment_oid: Optional[int] = None
        comment_type = 1
        if vtype == "ugc":
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 处理UGC视频，分P={p_index}")
            bvid = ident.get("bvid")
            aid = ident.get("aid")
            if bvid:
                logger.debug(f"[{self.name}] parse_bilibili_minimal: 使用BV号 {bvid}")
                info = await self.get_ugc_info(
                    bvid=bvid,
                    session=session,
                    cookie_header=cookie_header
                )
                pages = await self.get_pagelist(
                    bvid=bvid,
                    session=session,
                    cookie_header=cookie_header
                )
            elif aid:
                logger.debug(f"[{self.name}] parse_bilibili_minimal: 使用AV号 {aid}")
                info = await self.get_ugc_info(
                    aid=aid,
                    session=session,
                    cookie_header=cookie_header
                )
                pages = await self.get_pagelist(
                    aid=aid,
                    session=session,
                    cookie_header=cookie_header
                )
            else:
                raise RuntimeError(f"无法获取视频信息: {url}")
            comment_oid_raw = info.get("aid")
            if comment_oid_raw is None:
                comment_oid_raw = aid
            try:
                comment_oid = int(comment_oid_raw) if comment_oid_raw is not None else None
            except (TypeError, ValueError):
                comment_oid = None
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 视频信息获取成功，共{len(pages)}个分P")
            if p_index > len(pages):
                raise RuntimeError(f"分P序号超出范围: {p_index}")
            cid = pages[p_index - 1]["cid"]
            access_info = await self._analyze_target_access(
                vtype="ugc",
                referer=page_url,
                session=session,
                content_meta=info,
                cookie_header=cookie_header,
                bvid=bvid,
                aid=aid,
                cid=cid
            )
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 获取分P{cid}的直链")
            direct_url = await self._get_ugc_direct_url(
                bvid=bvid,
                aid=aid,
                cid=cid,
                referer=page_url,
                session=session,
                cookie_header=cookie_header
            )
            if not direct_url:
                raise RuntimeError(f"无法获取视频直链: {url}")
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 直链获取成功")
        elif vtype == "pgc":
            logger.debug(f"[{self.name}] parse_bilibili_minimal: 处理PGC番剧")
            FNVAL_MAX = 4048
            ep_id = ident.get("ep_id")
            if not ep_id:
                season_id = ident.get("season_id")
                if season_id:
                    ep_id = await self.get_first_ep_id_by_season(
                        season_id=season_id,
                        session=session,
                        cookie_header=cookie_header
                    )
                else:
                    raise RuntimeError(f"无法解析番剧标识: {url}")
            info = await self.get_pgc_info_by_ep(
                ep_id,
                session,
                cookie_header=cookie_header
            )
            comment_oid_raw = info.get("aid")
            try:
                comment_oid = int(comment_oid_raw) if comment_oid_raw is not None else None
            except (TypeError, ValueError):
                comment_oid = None
            access_info = await self._analyze_target_access(
                vtype="pgc",
                referer=page_url,
                session=session,
                content_meta=info,
                cookie_header=cookie_header,
                ep_id=ep_id
            )
            probe = await self.pgc_playurl_v2(
                ep_id,
                qn=120,
                fnval=FNVAL_MAX,
                referer=page_url,
                session=session,
                cookie_header=cookie_header
            )
            probe_payload = self._unwrap_playurl_data(probe)
            target_qn = (
                self.best_qn_from_data(probe) or
                probe_payload.get("quality") or
                80
            )
            merged_try = await self.pgc_playurl_v2(
                ep_id,
                qn=target_qn,
                fnval=0,
                referer=page_url,
                session=session,
                cookie_header=cookie_header
            )
            merged_payload = self._unwrap_playurl_data(merged_try)
            if merged_payload.get("durl"):
                direct_url = merged_payload["durl"][0].get("url")
            else:
                dash_try = await self.pgc_playurl_v2(
                    ep_id,
                    qn=target_qn,
                    fnval=FNVAL_MAX,
                    referer=page_url,
                    session=session,
                    cookie_header=cookie_header
                )
                dash_payload = self._unwrap_playurl_data(dash_try)
                direct_url = (
                    self._build_dash_download_url(dash_payload.get("dash") or {}) or
                    ""
                )
        else:
            raise RuntimeError(f"无法识别视频类型: {url}")
        if not direct_url:
            referer = page_url
            origin = "https://www.bilibili.com"
            image_headers, video_headers = self._build_media_headers(
                referer=referer,
                origin=origin,
                cookie_header=cookie_header
            )
            result = {
                "url": original_url if urlparse(original_url).netloc.lower() == B23_HOST else page_url,
                "title": info.get("title", ""),
                "author": info.get("author", ""),
                "desc": info.get("desc", ""),
                "timestamp": info.get("timestamp", ""),
                "video_urls": [],
                "image_urls": [],
                "image_headers": image_headers,
                "video_headers": video_headers,
            }
            result.update(self._access_fields_from_info(access_info))
            if result.get("access_status") in ("preview_only", "restricted", "unavailable"):
                if enable_hot_comments:
                    await self._attach_hot_comments_to_result(
                        session=session,
                        result=result,
                        oid=comment_oid,
                        comment_type=comment_type,
                        referer=page_url,
                        cookie_header=cookie_header
                    )
                return result
            raise RuntimeError(f"无法获取视频直链: {url}")
        is_b23_short = urlparse(original_url).netloc.lower() == B23_HOST
        display_url = original_url if is_b23_short else page_url
        
        referer = page_url
        origin = "https://www.bilibili.com"
        image_headers, video_headers = self._build_media_headers(
            referer=referer,
            origin=origin,
            cookie_header=cookie_header
        )
        result = {
            "url": display_url,
            "title": info.get("title", ""),
            "author": info.get("author", ""),
            "desc": info.get("desc", ""),
            "timestamp": info.get("timestamp", ""),
            "video_urls": self._add_range_prefix_to_video_urls([[direct_url]]),
            "image_urls": [],
            "image_headers": image_headers,
            "video_headers": video_headers,
        }
        result.update(self._access_fields_from_info(access_info))
        if enable_hot_comments:
            await self._attach_hot_comments_to_result(
                session=session,
                result=result,
                oid=comment_oid,
                comment_type=comment_type,
                referer=page_url,
                cookie_header=cookie_header
            )
        logger.debug(f"[{self.name}] parse_bilibili_minimal: 解析完成 {url}, title={result.get('title', '')[:50]}")
        return result

