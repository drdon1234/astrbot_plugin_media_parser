# -*- coding: utf-8 -*-
import asyncio
import re
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, parse_qs

import aiohttp

from .base_parser import BaseVideoParser

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
B23_HOST = "b23.tv"
BV_RE = re.compile(r"[Bb][Vv][0-9A-Za-z]{10,}", re.IGNORECASE)
AV_RE = re.compile(r"[Aa][Vv](\d+)", re.IGNORECASE)
EP_PATH_RE = re.compile(r"/bangumi/play/ep(\d+)", re.IGNORECASE)
EP_QS_RE = re.compile(r"(?:^|[?&])ep_id=(\d+)", re.IGNORECASE)
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
XOR_CODE = 23442827791579
MAX_AID = 1 << 51
BASE = 58


def av2bv(av: int) -> str:
    """将AV号转换为BV号。

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
    """B站视频解析器。"""

    def __init__(self):
        """初始化B站解析器。"""
        super().__init__("B站")
        self.semaphore = asyncio.Semaphore(10)
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com"
        }

    def _prepare_aid_param(self, aid: str) -> int:
        """将aid转换为整数。

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
        """检查并解析JSON响应。

        Args:
            resp: HTTP响应对象

        Returns:
            JSON响应字典

        Raises:
            RuntimeError: 当响应不是JSON格式时
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
        """处理API响应，检查错误码。

        Args:
            j: API响应JSON字典
            api_name: API名称

        Raises:
            RuntimeError: 当API返回错误码时
        """
        if j.get("code") != 0:
            error_msg = j.get('message', '未知错误')
            error_code = j.get('code')
            raise RuntimeError(
                f"{api_name} error: {error_code} {error_msg}"
            )

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL（仅支持静态视频：普通视频和番剧）。

        Args:
            url: 视频链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'live.bilibili.com' in url_lower:
            return False
        if '/dynamic/' in url_lower or '/opus/' in url_lower:
            return False
        if 'space.bilibili.com' in url_lower:
            return False
        if B23_HOST in urlparse(url).netloc.lower():
            return True
        if BV_RE.search(url):
            return True
        if AV_RE.search(url):
            return True
        if EP_PATH_RE.search(url) or EP_QS_RE.search(url):
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取B站链接，最大程度兼容各种格式。

        Args:
            text: 输入文本

        Returns:
            B站链接列表
        """
        result_links = []
        seen_ids = set()
        b23_pattern = r'https?://[Bb]23\.tv/[^\s<>"\'()]+'
        b23_links = re.findall(b23_pattern, text, re.IGNORECASE)
        result_links.extend(b23_links)
        bilibili_domains = r'(?:www|m|mobile)\.bilibili\.com'
        bv_url_pattern = (
            rf'https?://{bilibili_domains}/video/'
            rf'[Bb][Vv][0-9A-Za-z]{{10,}}[^\s<>"\'()]*'
        )
        bv_url_matches = re.finditer(bv_url_pattern, text, re.IGNORECASE)
        for match in bv_url_matches:
            url = match.group(0)
            url_lower = url.lower()
            if '/dynamic/' in url_lower or '/opus/' in url_lower:
                continue
            normalized = url.lower().replace(
                'm.bilibili.com',
                'www.bilibili.com'
            )
            normalized = normalized.replace(
                'mobile.bilibili.com',
                'www.bilibili.com'
            )
            bv_match = BV_RE.search(url)
            if bv_match:
                bvid = bv_match.group(0)
                if bvid[0:2].upper() != "BV":
                    bvid = "BV" + bvid[2:]
                seen_ids.add(f"BV:{bvid}")
                normalized_url = f"https://www.bilibili.com/video/{bvid}"
                if normalized_url not in result_links:
                    result_links.append(normalized_url)
        av_url_pattern = (
            rf'https?://{bilibili_domains}/video/'
            rf'[Aa][Vv](\d+)[^\s<>"\'()]*'
        )
        av_url_matches = re.finditer(av_url_pattern, text, re.IGNORECASE)
        for match in av_url_matches:
            url = match.group(0)
            url_lower = url.lower()
            if '/dynamic/' in url_lower or '/opus/' in url_lower:
                continue
            av_num = match.group(1)
            seen_ids.add(f"AV:{av_num}")
            av_url = f"https://www.bilibili.com/video/av{av_num}"
            if av_url not in result_links:
                result_links.append(av_url)
        ep_url_pattern = (
            rf'https?://{bilibili_domains}/bangumi/play/'
            rf'ep(\d+)[^\s<>"\'()]*'
        )
        ep_url_matches = re.finditer(ep_url_pattern, text, re.IGNORECASE)
        for match in ep_url_matches:
            ep_id = match.group(1)
            ep_url = f"https://www.bilibili.com/bangumi/play/ep{ep_id}"
            if ep_url not in result_links:
                result_links.append(ep_url)
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
            if f"BV:{bvid}" not in seen_ids:
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                if ('http://' not in context.lower() and
                        'https://' not in context.lower()):
                    seen_ids.add(f"BV:{bvid}")
                    bv_url = f"https://www.bilibili.com/video/{bvid}"
                    if bv_url not in result_links:
                        result_links.append(bv_url)
        av_standalone_pattern = r'\b[Aa][Vv](\d+)\b'
        av_standalone_matches = re.finditer(
            av_standalone_pattern,
            text,
            re.IGNORECASE
        )
        for match in av_standalone_matches:
            av_num = match.group(1)
            if f"AV:{av_num}" not in seen_ids:
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                if ('http://' not in context.lower() and
                        'https://' not in context.lower()):
                    seen_ids.add(f"AV:{av_num}")
                    av_url = f"https://www.bilibili.com/video/av{av_num}"
                    if av_url not in result_links:
                        result_links.append(av_url)
        return result_links

    async def expand_b23(
        self,
        url: str,
        session: aiohttp.ClientSession
    ) -> str:
        """展开b23短链。

        Args:
            url: 原始URL
            session: aiohttp会话

        Returns:
            展开后的URL，如果展开失败返回原URL
        """
        if urlparse(url).netloc.lower() == B23_HOST:
            headers = {
                "User-Agent": UA,
                "Referer": "https://www.bilibili.com"
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
        """提取分P序号。

        Args:
            url: 视频URL

        Returns:
            分P序号，默认为1
        """
        try:
            return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
        except Exception:
            return 1

    def detect_target(
        self,
        url: str
    ) -> Tuple[Optional[str], Dict[str, str]]:
        """检测视频类型和标识符（支持视频和番剧）。

        Args:
            url: 视频URL

        Returns:
            包含视频类型和标识符字典的元组
            (视频类型: "ugc"或"pgc", 标识符字典)
        """
        m = EP_PATH_RE.search(url) or EP_QS_RE.search(url)
        if m:
            return "pgc", {"ep_id": m.group(1)}
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
        session: aiohttp.ClientSession = None
    ) -> Dict[str, str]:
        """获取UGC视频信息。

        Args:
            bvid: BV号
            aid: AV号
            session: aiohttp会话

        Returns:
            包含title、desc、author的字典

        Raises:
            ValueError: 当bvid和aid都未提供时
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
            headers=self._default_headers,
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
        return {"title": title, "desc": desc, "author": author}

    async def get_pgc_info_by_ep(
        self,
        ep_id: str,
        session: aiohttp.ClientSession
    ) -> Dict[str, str]:
        """获取PGC视频信息。

        Args:
            ep_id: 番剧集ID
            session: aiohttp会话

        Returns:
            包含title、desc、author的字典

        Raises:
            RuntimeError: 当API返回错误时
        """
        api = "https://api.bilibili.com/pgc/view/web/season"
        async with session.get(
            api,
            params={"ep_id": ep_id},
            headers=self._default_headers,
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
        return {"title": title, "desc": desc, "author": author}

    async def get_pagelist(
        self,
        bvid: str = None,
        aid: str = None,
        session: aiohttp.ClientSession = None
    ):
        """获取分P列表。

        Args:
            bvid: BV号
            aid: AV号
            session: aiohttp会话

        Returns:
            分P列表数据

        Raises:
            ValueError: 当bvid和aid都未提供时
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
            headers=self._default_headers,
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
        session: aiohttp.ClientSession = None
    ):
        """获取UGC视频播放地址（优先使用BV号，aid作为备用）。

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
            ValueError: 当bvid和aid都未提供时
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
        headers = {**self._default_headers, "Referer": referer}
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
        session: aiohttp.ClientSession
    ):
        """获取PGC视频播放地址。

        Args:
            ep_id: 番剧集ID
            qn: 画质
            fnval: 视频流格式
            referer: 引用页面URL
            session: aiohttp会话

        Returns:
            播放地址数据

        Raises:
            RuntimeError: 当API返回错误时
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
        headers = {**self._default_headers, "Referer": referer}
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
        """从数据中获取最佳画质。

        Args:
            data: 播放地址数据

        Returns:
            最佳画质代码，如果无法获取返回None
        """
        aq = data.get("accept_quality") or []
        if isinstance(aq, list) and aq:
            try:
                return max(int(x) for x in aq)
            except Exception:
                pass
        dash = data.get("dash") or {}
        if dash.get("video"):
            try:
                return max(int(v.get("id", 0)) for v in dash["video"])
            except Exception:
                pass
        return None

    def pick_best_video(self, dash_obj: Dict[str, Any]):
        """选择最佳视频流。

        Args:
            dash_obj: DASH格式视频数据

        Returns:
            最佳视频流数据，如果未找到返回None
        """
        vids = dash_obj.get("video") or []
        if not vids:
            return None
        return sorted(
            vids,
            key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)),
            reverse=True
        )[0]

    async def _get_ugc_direct_url(
        self,
        bvid: str = None,
        aid: str = None,
        cid: int = None,
        referer: str = None,
        session: aiohttp.ClientSession = None
    ) -> Optional[str]:
        """获取UGC视频直链（统一处理bvid和aid）。

        Args:
            bvid: BV号（优先）
            aid: AV号（备用）
            cid: 分P的cid
            referer: 引用页面URL
            session: aiohttp会话

        Returns:
            视频直链，如果失败返回None
        """
        FNVAL_MAX = 4048
        if bvid:
            probe = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=120,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session
            )
        else:
            probe = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=120,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session
            )
        target_qn = (
            self.best_qn_from_data(probe) or
            probe.get("quality") or
            80
        )
        if bvid:
            merged_try = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=target_qn,
                fnval=0,
                referer=referer,
                session=session
            )
        else:
            merged_try = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=target_qn,
                fnval=0,
                referer=referer,
                session=session
            )
        if merged_try.get("durl"):
            return merged_try["durl"][0].get("url")
        if bvid:
            dash_try = await self.ugc_playurl(
                bvid=bvid,
                cid=cid,
                qn=target_qn,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session
            )
        else:
            dash_try = await self.ugc_playurl(
                aid=aid,
                cid=cid,
                qn=target_qn,
                fnval=FNVAL_MAX,
                referer=referer,
                session=session
            )
        v = self.pick_best_video(dash_try.get("dash") or {})
        return (v.get("baseUrl") or v.get("base_url")) if v else None


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个B站链接。

        Args:
            session: aiohttp会话
            url: B站链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        async with self.semaphore:
            return await self.parse_bilibili_minimal(url, session=session)

    async def parse_bilibili_minimal(
        self,
        url: str,
        p: Optional[int] = None,
        session: aiohttp.ClientSession = None
    ) -> Optional[Dict[str, Any]]:
        """解析B站链接，返回视频信息。

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
                return await self.parse_bilibili_minimal(url, p, sess)
        original_url = url
        page_url = await self.expand_b23(url, session)
        if not self.can_parse(page_url):
            raise RuntimeError(f"无法解析此URL: {url}")
        p_index = max(1, int(p or self.extract_p(page_url)))
        vtype, ident = self.detect_target(page_url)
        if not vtype:
            raise RuntimeError(f"无法识别视频类型: {url}")
        if vtype == "ugc":
            bvid = ident.get("bvid")
            aid = ident.get("aid")
            if bvid:
                info = await self.get_ugc_info(bvid=bvid, session=session)
                pages = await self.get_pagelist(bvid=bvid, session=session)
            elif aid:
                info = await self.get_ugc_info(aid=aid, session=session)
                pages = await self.get_pagelist(aid=aid, session=session)
            else:
                raise RuntimeError(f"无法获取视频信息: {url}")
            if p_index > len(pages):
                raise RuntimeError(f"分P序号超出范围: {p_index}")
            cid = pages[p_index - 1]["cid"]
            direct_url = await self._get_ugc_direct_url(
                bvid=bvid,
                aid=aid,
                cid=cid,
                referer=page_url,
                session=session
            )
            if not direct_url:
                raise RuntimeError(f"无法获取视频直链: {url}")
        elif vtype == "pgc":
            FNVAL_MAX = 4048
            ep_id = ident["ep_id"]
            info = await self.get_pgc_info_by_ep(ep_id, session)
            probe = await self.pgc_playurl_v2(
                ep_id,
                qn=120,
                fnval=FNVAL_MAX,
                referer=page_url,
                session=session
            )
            target_qn = (
                self.best_qn_from_data(probe) or
                probe.get("quality") or
                80
            )
            merged_try = await self.pgc_playurl_v2(
                ep_id,
                qn=target_qn,
                fnval=0,
                referer=page_url,
                session=session
            )
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await self.pgc_playurl_v2(
                    ep_id,
                    qn=target_qn,
                    fnval=FNVAL_MAX,
                    referer=page_url,
                    session=session
                )
                v = self.pick_best_video(dash_try.get("dash") or {})
                direct_url = (
                    (v.get("baseUrl") or v.get("base_url")) if v else ""
                )
        else:
            raise RuntimeError(f"无法识别视频类型: {url}")
        if not direct_url:
            raise RuntimeError(f"无法获取视频直链: {url}")
        is_b23_short = urlparse(original_url).netloc.lower() == B23_HOST
        display_url = original_url if is_b23_short else page_url
        
        return {
            "url": display_url,
            "media_type": "video",
            "title": info.get("title", ""),
            "author": info.get("author", ""),
            "desc": info.get("desc", ""),
            "timestamp": "",  # B站API不返回发布时间
            "media_urls": [direct_url],
            "thumb_url": None,  # B站视频没有单独的封面图URL
            "page_url": page_url,  # 完整页面URL，用于检测视频大小时的referer
        }

