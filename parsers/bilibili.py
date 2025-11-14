# -*- coding: utf-8 -*-
import asyncio
import json
import re
from datetime import datetime
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
OPUS_RE = re.compile(r"/opus/(\d+)", re.IGNORECASE)
T_BILIBILI_RE = re.compile(r"t\.bilibili\.com/(\d+)", re.IGNORECASE)
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"
XOR_CODE = 23442827791579
MAX_AID = 1 << 51
BASE = 58


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
    """B站视频解析器"""

    def __init__(self):
        """初始化B站解析器"""
        super().__init__("B站")
        self.semaphore = asyncio.Semaphore(10)
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com"
        }

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
        """处理API响应，检查错误码

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
        """判断是否可以解析此URL（支持视频和动态链接）

        Args:
            url: 视频或动态链接

        Returns:
            如果可以解析返回True，否则返回False
        """
        if not url:
            return False
        url_lower = url.lower()
        if 'live.bilibili.com' in url_lower:
            return False
        if 'space.bilibili.com' in url_lower:
            return False

        if '/opus/' in url_lower:
            return True
        if 't.bilibili.com' in url_lower:
            return True

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

        return list(result_links_set)

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
            动态ID，如果提取失败返回None
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
        referer: str = None
    ) -> Dict[str, Any]:
        """获取opus动态信息

        Args:
            opus_id: opus ID（动态ID）
            session: aiohttp会话
            referer: 引用页面URL

        Returns:
            动态信息字典

        Raises:
            RuntimeError: 当API返回错误时
        """
        api = "https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail"
        params = {"dynamic_id": opus_id}
        headers = dict(self._default_headers)
        if referer:
            headers["Referer"] = referer
        else:
            headers["Referer"] = f"https://www.bilibili.com/opus/{opus_id}"

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
            视频链接，如果提取失败返回None
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
        """获取UGC视频信息

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
        
        timestamp = ""
        pubdate = data.get("pubdate")
        if pubdate:
            dt = datetime.fromtimestamp(int(pubdate))
            timestamp = dt.strftime("%Y-%m-%d")
        
        return {"title": title, "desc": desc, "author": author, "timestamp": timestamp}

    async def get_pgc_info_by_ep(
        self,
        ep_id: str,
        session: aiohttp.ClientSession
    ) -> Dict[str, str]:
        """获取PGC视频信息

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
        
        timestamp = ""
        if ep_obj:
            pub_time = ep_obj.get("pub_time")
            if pub_time:
                dt = datetime.fromtimestamp(int(pub_time))
                timestamp = dt.strftime("%Y-%m-%d")
        
        return {"title": title, "desc": desc, "author": author, "timestamp": timestamp}

    async def get_pagelist(
        self,
        bvid: str = None,
        aid: str = None,
        session: aiohttp.ClientSession = None
    ):
        """获取分P列表

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
        """从数据中获取最佳画质

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
        """选择最佳视频流

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
        """获取UGC视频直链（统一处理bvid和aid）

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

    async def parse_opus(
        self,
        url: str,
        session: aiohttp.ClientSession
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

        data = await self.get_opus_info(opus_id, session, referer=url)

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
            video_result = await self.parse_bilibili_minimal(video_url, session=session)

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

                return {
                    "url": final_url,
                    "media_type": video_result.get("media_type", "video"),
                    "title": final_title,
                    "author": final_author,
                    "desc": final_desc,
                    "timestamp": final_timestamp,
                    "media_urls": video_result.get("media_urls", []),
                }
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

                return {
                    "url": original_url if B23_HOST in urlparse(original_url).netloc.lower() else url,
                    "media_type": video_result.get("media_type", "video"),
                    "title": final_title,
                    "author": author,
                    "desc": final_desc,
                    "timestamp": timestamp,
                    "media_urls": video_result.get("media_urls", []),
                }

        media_type = "image"
        media_urls = []
        if isinstance(item, dict):
            pictures = item.get("pictures", [])
            if isinstance(pictures, list):
                for pic in pictures:
                    if isinstance(pic, dict):
                        pic_url = pic.get("img_src") or pic.get("imgSrc") or pic.get("url")
                        if pic_url:
                            media_urls.append(pic_url)
                    elif isinstance(pic, str):
                        media_urls.append(pic)

            if len(media_urls) > 1:
                media_type = "gallery"
            elif len(media_urls) == 1:
                media_type = "image"
            else:
                media_type = "image"

        if not media_urls:
            media_urls = []

        display_url = original_url if B23_HOST in urlparse(original_url).netloc.lower() else url

        return {
            "url": display_url,
            "media_type": media_type,
            "title": title,
            "author": author,
            "desc": desc,
            "timestamp": timestamp,
            "media_urls": media_urls,
        }

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
        async with self.semaphore:
            return await self.parse_bilibili_minimal(url, session=session)

    async def parse_bilibili_minimal(
        self,
        url: str,
        p: Optional[int] = None,
        session: aiohttp.ClientSession = None
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
                return await self.parse_bilibili_minimal(url, p, sess)
        original_url = url
        page_url = await self.expand_b23(url, session)

        page_url_lower = page_url.lower()
        if '/opus/' in page_url_lower or 't.bilibili.com' in page_url_lower:
            return await self.parse_opus(page_url, session)

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
            "timestamp": info.get("timestamp", ""),
            "media_urls": [direct_url],
            "thumb_url": None,
            "page_url": page_url,
        }

