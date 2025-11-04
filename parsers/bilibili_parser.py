# -*- coding: utf-8 -*-
import re
import asyncio
from typing import Optional, Dict, Any, Tuple, List
from urllib.parse import urlparse, parse_qs
import aiohttp
from ..base_parser import BaseVideoParser

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

B23_HOST = "b23.tv"
# BV号正则：大小写不敏感，支持BV/bv/Bv等
BV_RE = re.compile(r"[Bb][Vv][0-9A-Za-z]{10,}", re.IGNORECASE)
# AV号正则：大小写不敏感，提取数字部分
AV_RE = re.compile(r"[Aa][Vv](\d+)", re.IGNORECASE)
# 番剧链接正则
EP_PATH_RE = re.compile(r"/bangumi/play/ep(\d+)", re.IGNORECASE)
EP_QS_RE = re.compile(r"(?:^|[?&])ep_id=(\d+)", re.IGNORECASE)

# BV号编码表（新算法）
BV_TABLE = "FcwAPNKTMug3GV5Lj7EJnHpWsx4tb8haYeviqBz6rkCy12mUSDQX9RdoZf"

# 常量定义
XOR_CODE = 23442827791579
MAX_AID = 1 << 51  # 2^51
BASE = 58


def av2bv(av: int) -> str:
    """
    将AV号转换为BV号
    参考: https://github.com/SocialSisterYi/bilibili-API-collect/blob/master/docs/misc/bvid_desc.md
    
    Args:
        av: AV号（整数）
        
    Returns:
        BV号字符串
    """
    # 初始化数组
    bytes_arr = ['B', 'V', '1', '0', '0', '0', '0', '0', '0', '0', '0', '0']
    bv_idx = len(bytes_arr) - 1
    
    # 计算tmp: (MAX_AID | av) ^ XOR_CODE
    tmp = (MAX_AID | av) ^ XOR_CODE
    
    # 将tmp转换为58进制，从后往前填入
    while tmp > 0:
        bytes_arr[bv_idx] = BV_TABLE[tmp % BASE]
        tmp = tmp // BASE
        bv_idx -= 1
    
    # 交换位置：索引3和9，索引4和7
    bytes_arr[3], bytes_arr[9] = bytes_arr[9], bytes_arr[3]
    bytes_arr[4], bytes_arr[7] = bytes_arr[7], bytes_arr[4]
    
    return ''.join(bytes_arr)


class BilibiliParser(BaseVideoParser):
    """B站视频解析器"""
    
    def __init__(self, max_video_size_mb: float = 0.0):
        super().__init__("B站", max_video_size_mb)
        self.semaphore = asyncio.Semaphore(10)
        # 统一的请求头
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com",
            "Origin": "https://www.bilibili.com"
        }
    
    def _prepare_aid_param(self, aid: str) -> int:
        """将aid转换为整数"""
        try:
            return int(aid) if isinstance(aid, str) else aid
        except (ValueError, TypeError):
            return aid
    
    async def _check_json_response(self, resp: aiohttp.ClientResponse) -> dict:
        """检查并解析JSON响应"""
        if resp.content_type != 'application/json':
            text = await resp.text()
            raise RuntimeError(f"API返回非JSON响应 (状态码: {resp.status}, Content-Type: {resp.content_type}): {text[:200]}")
        return await resp.json()
    
    async def _handle_api_response(self, j: dict, api_name: str) -> None:
        """处理API响应，检查错误码"""
        if j.get("code") != 0:
            error_msg = j.get('message', '未知错误')
            error_code = j.get('code')
            raise RuntimeError(f"{api_name} error: {error_code} {error_msg}")

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL（仅支持静态视频：普通视频和番剧）"""
        if not url:
            return False
        
        # 排除非静态视频链接
        url_lower = url.lower()
        # 排除直播链接
        if 'live.bilibili.com' in url_lower:
            return False
        # 排除动态链接
        if '/dynamic/' in url_lower or '/opus/' in url_lower:
            return False
        # 排除空间链接
        if 'space.bilibili.com' in url_lower:
            return False
        
        # 检查是否为b23短链
        if B23_HOST in urlparse(url).netloc.lower():
            return True
        # 检查是否包含BV号
        if BV_RE.search(url):
            return True
        # 检查是否包含AV号
        if AV_RE.search(url):
            return True
        # 检查是否为番剧链接
        if EP_PATH_RE.search(url) or EP_QS_RE.search(url):
            return True
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取B站链接，最大程度兼容各种格式"""
        result_links = []
        seen_ids = set()  # 用于去重：记录已提取的BV/AV号
        
        # b23短链（支持大小写和http/https）
        b23_pattern = r'https?://[Bb]23\.tv/[^\s<>"\'()]+'
        b23_links = re.findall(b23_pattern, text, re.IGNORECASE)
        result_links.extend(b23_links)
        
        # B站各种域名和格式的链接
        # 支持：www.bilibili.com, m.bilibili.com, mobile.bilibili.com
        # 排除：live.bilibili.com（直播）、space.bilibili.com（空间）
        bilibili_domains = r'(?:www|m|mobile)\.bilibili\.com'
        
        # 完整的BV号链接（支持各种格式和参数，仅视频类型）
        bv_url_pattern = rf'https?://{bilibili_domains}/video/[Bb][Vv][0-9A-Za-z]{{10,}}[^\s<>"\'()]*'
        bv_url_matches = re.finditer(bv_url_pattern, text, re.IGNORECASE)
        for match in bv_url_matches:
            url = match.group(0)
            # 排除非视频链接（虽然域名已限制，但为安全起见仍检查）
            url_lower = url.lower()
            if '/dynamic/' in url_lower or '/opus/' in url_lower:
                continue
            # 标准化URL
            normalized = url.lower().replace('m.bilibili.com', 'www.bilibili.com')
            normalized = normalized.replace('mobile.bilibili.com', 'www.bilibili.com')
            # 提取BV号并标准化
            bv_match = BV_RE.search(url)  # 从原始URL提取，保持大小写
            if bv_match:
                bvid = bv_match.group(0)
                # 只确保前缀是BV（大写），后面的字符保持原样
                if bvid[0:2].upper() != "BV":
                    bvid = "BV" + bvid[2:]
                # 保持原始大小写
                seen_ids.add(f"BV:{bvid}")
                normalized_url = f"https://www.bilibili.com/video/{bvid}"
                if normalized_url not in result_links:
                    result_links.append(normalized_url)
        
        # 完整的AV号链接（支持各种格式和参数）
        av_url_pattern = rf'https?://{bilibili_domains}/video/[Aa][Vv](\d+)[^\s<>"\'()]*'
        av_url_matches = re.finditer(av_url_pattern, text, re.IGNORECASE)
        for match in av_url_matches:
            url = match.group(0)
            # 排除非视频链接
            url_lower = url.lower()
            if '/dynamic/' in url_lower or '/opus/' in url_lower:
                continue
            av_num = match.group(1)
            seen_ids.add(f"AV:{av_num}")
            av_url = f"https://www.bilibili.com/video/av{av_num}"
            if av_url not in result_links:
                result_links.append(av_url)
        
        # 番剧链接（支持各种格式和参数）
        ep_url_pattern = rf'https?://{bilibili_domains}/bangumi/play/ep(\d+)[^\s<>"\'()]*'
        ep_url_matches = re.finditer(ep_url_pattern, text, re.IGNORECASE)
        for match in ep_url_matches:
            ep_id = match.group(1)
            ep_url = f"https://www.bilibili.com/bangumi/play/ep{ep_id}"
            if ep_url not in result_links:
                result_links.append(ep_url)
        
        # 单独的BV号（不区分大小写，支持在文本中单独出现）
        # 使用更宽松的匹配，但排除已经在完整链接中出现的
        bv_standalone_pattern = r'\b[Bb][Vv][0-9A-Za-z]{10,}\b'
        bv_standalone_matches = re.finditer(bv_standalone_pattern, text, re.IGNORECASE)
        for match in bv_standalone_matches:
            bvid = match.group(0)
            # 只确保前缀是BV（大写），后面的字符保持原样
            if bvid[0:2].upper() != "BV":
                bvid = "BV" + bvid[2:]
            # 保持原始大小写
            # 检查是否已经在完整URL中出现
            if f"BV:{bvid}" not in seen_ids:
                # 检查是否在URL中（避免重复提取）
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                # 如果周围没有http://或https://，认为是独立出现的
                if 'http://' not in context.lower() and 'https://' not in context.lower():
                    seen_ids.add(f"BV:{bvid}")
                    bv_url = f"https://www.bilibili.com/video/{bvid}"
                    if bv_url not in result_links:
                        result_links.append(bv_url)
        
        # 单独的AV号（不区分大小写）
        # 使用更宽松的匹配，但排除已经在完整链接中出现的
        av_standalone_pattern = r'\b[Aa][Vv](\d+)\b'
        av_standalone_matches = re.finditer(av_standalone_pattern, text, re.IGNORECASE)
        for match in av_standalone_matches:
            av_num = match.group(1)
            # 检查是否已经在完整URL中出现
            if f"AV:{av_num}" not in seen_ids:
                # 检查是否在URL中（避免重复提取）
                start_pos = match.start()
                context_start = max(0, start_pos - 50)
                context_end = min(len(text), match.end() + 10)
                context = text[context_start:context_end]
                # 如果周围没有http://或https://，认为是独立出现的
                if 'http://' not in context.lower() and 'https://' not in context.lower():
                    seen_ids.add(f"AV:{av_num}")
                    av_url = f"https://www.bilibili.com/video/av{av_num}"
                    if av_url not in result_links:
                        result_links.append(av_url)
        
        return result_links

    async def expand_b23(self, url: str, session: aiohttp.ClientSession) -> str:
        """展开b23短链"""
        if urlparse(url).netloc.lower() == B23_HOST:
            headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}
            try:
                async with session.get(url, headers=headers, allow_redirects=True, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    expanded_url = str(r.url)
                    return expanded_url
            except Exception as e:
                return url
        return url

    def extract_p(self, url: str) -> int:
        """提取分P序号"""
        try:
            return int(parse_qs(urlparse(url).query).get("p", ["1"])[0])
        except Exception:
            return 1

    def detect_target(self, url: str) -> Tuple[Optional[str], Dict[str, str]]:
        """检测视频类型和标识符（支持视频和番剧）"""
        # 检查番剧链接
        m = EP_PATH_RE.search(url) or EP_QS_RE.search(url)
        if m:
            return "pgc", {"ep_id": m.group(1)}
        # 检查BV号
        m = BV_RE.search(url)
        if m:
            # 保持BV号的原始大小写（B站BV号有特定编码规则，不能随意转换大小写）
            bvid = m.group(0)
            # 只确保前缀是BV（大写），后面的字符保持原样
            if bvid[0:2].upper() != "BV":
                bvid = "BV" + bvid[2:]
            # 保持原始大小写，不进行任何转换
            return "ugc", {"bvid": bvid}
        # 检查AV号，转换为BV号统一处理
        m = AV_RE.search(url)
        if m:
            try:
                aid = int(m.group(1))
                # 将AV号转换为BV号
                bvid = av2bv(aid)
                return "ugc", {"bvid": bvid}
            except (ValueError, OverflowError) as e:
                # 转换失败时，仍然使用aid（向后兼容）
                return "ugc", {"aid": m.group(1)}
        return None, {}

    async def get_ugc_info(self, bvid: str = None, aid: str = None, session: aiohttp.ClientSession = None) -> Dict[str, str]:
        """获取UGC视频信息"""
        api = "https://api.bilibili.com/x/web-interface/view"
        params = {}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        async with session.get(api, params=params, headers=self._default_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "view")
        data = j["data"]
        title = data.get("title") or ""
        desc = data.get("desc") or ""
        owner = data.get("owner") or {}
        name = owner.get("name") or ""
        mid = owner.get("mid")
        author = f"{name}(uid:{mid})" if name else ""
        return {"title": title, "desc": desc, "author": author}

    async def get_pgc_info_by_ep(self, ep_id: str, session: aiohttp.ClientSession) -> Dict[str, str]:
        """获取PGC视频信息"""
        api = "https://api.bilibili.com/pgc/view/web/season"
        async with session.get(api, params={"ep_id": ep_id}, headers=self._default_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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
            title = ep_obj.get("share_copy") or ep_obj.get("long_title") or ep_obj.get("title") or ""
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
        author = f"{name}({mid})" if name else (result.get("season_title") or result.get("title") or "")
        return {"title": title, "desc": desc, "author": author}

    async def get_pagelist(self, bvid: str = None, aid: str = None, session: aiohttp.ClientSession = None):
        """获取分P列表"""
        api = "https://api.bilibili.com/x/player/pagelist"
        params = {"jsonp": "json"}
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        async with session.get(api, params=params, headers=self._default_headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pagelist")
        return j["data"]

    async def ugc_playurl(self, bvid: str = None, aid: str = None, cid: int = None, qn: int = None, fnval: int = None, referer: str = None, session: aiohttp.ClientSession = None):
        """获取UGC视频播放地址（优先使用BV号，aid作为备用）"""
        api = "https://api.bilibili.com/x/player/playurl"
        params = {
            "cid": cid, "qn": qn, "fnver": 0, "fnval": fnval,
            "fourk": 1, "otype": "json", "platform": "html5", "high_quality": 1
        }
        if bvid:
            params["bvid"] = bvid
        elif aid:
            params["aid"] = self._prepare_aid_param(aid)
        else:
            raise ValueError("必须提供bvid或aid参数")
        
        headers = {**self._default_headers, "Referer": referer}
        async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "playurl")
        return j["data"]

    async def pgc_playurl_v2(self, ep_id: str, qn: int, fnval: int, referer: str, session: aiohttp.ClientSession):
        """获取PGC视频播放地址"""
        api = "https://api.bilibili.com/pgc/player/web/v2/playurl"
        params = {"ep_id": ep_id, "qn": qn, "fnver": 0, "fnval": fnval, "fourk": 1, "otype": "json"}
        headers = {**self._default_headers, "Referer": referer}
        async with session.get(api, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            j = await self._check_json_response(resp)
        await self._handle_api_response(j, "pgc playurl v2")
        return j.get("result") or j.get("data") or j

    def best_qn_from_data(self, data: Dict[str, Any]) -> Optional[int]:
        """从数据中获取最佳画质"""
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
        """选择最佳视频流"""
        vids = dash_obj.get("video") or []
        if not vids:
            return None
        return sorted(vids, key=lambda x: (x.get("id", 0), x.get("bandwidth", 0)), reverse=True)[0]

    async def parse(self, session: aiohttp.ClientSession, url: str) -> Optional[Dict[str, Any]]:
        """解析单个B站链接"""
        async with self.semaphore:
            try:
                return await self.parse_bilibili_minimal(url, session=session)
            except Exception as e:
                return None

    async def parse_bilibili_minimal(self, url: str, p: Optional[int] = None, session: aiohttp.ClientSession = None) -> Optional[Dict[str, Any]]:
        """解析B站链接，返回视频信息"""
        if session is None:
            timeout = aiohttp.ClientTimeout(total=10)
            async with aiohttp.ClientSession(headers={"User-Agent": UA}, timeout=timeout) as sess:
                return await self.parse_bilibili_minimal(url, p, sess)
        
        page_url = await self.expand_b23(url, session)
        # 展开b23短链后，再次检查是否为可解析的静态视频链接
        if not self.can_parse(page_url):
            return None
        p_index = max(1, int(p or self.extract_p(page_url)))
        vtype, ident = self.detect_target(page_url)
        if not vtype:
            return None

        FNVAL_MAX = 4048
        if vtype == "ugc":
            # 普通视频（UGC）- 统一使用BV号处理
            bvid = ident.get("bvid")
            aid = ident.get("aid")  # 备用，如果转换失败时使用
            
            # 优先使用BV号（AV号已转换为BV号）
            if bvid:
                info = await self.get_ugc_info(bvid=bvid, session=session)
                pages = await self.get_pagelist(bvid=bvid, session=session)
            elif aid:
                # 如果BV号转换失败，使用aid（向后兼容）
                info = await self.get_ugc_info(aid=aid, session=session)
                pages = await self.get_pagelist(aid=aid, session=session)
            else:
                return None
            
            if p_index > len(pages):
                return None
            cid = pages[p_index - 1]["cid"]
            
            # 统一使用BV号调用playurl API
            if bvid:
                # 使用BV号的标准流程
                probe = await self.ugc_playurl(bvid=bvid, cid=cid, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
                target_qn = self.best_qn_from_data(probe) or probe.get("quality") or 80

                # 先尝试合并格式
                merged_try = await self.ugc_playurl(bvid=bvid, cid=cid, qn=target_qn, fnval=0, referer=page_url, session=session)
                if merged_try.get("durl"):
                    direct_url = merged_try["durl"][0].get("url")
                else:
                    # 尝试DASH格式
                    dash_try = await self.ugc_playurl(bvid=bvid, cid=cid, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session)
                    v = self.pick_best_video(dash_try.get("dash") or {})
                    direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""
            else:
                # 备用方案：使用aid（向后兼容）
                probe = await self.ugc_playurl(aid=aid, cid=cid, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
                target_qn = self.best_qn_from_data(probe) or probe.get("quality") or 80

                merged_try = await self.ugc_playurl(aid=aid, cid=cid, qn=target_qn, fnval=0, referer=page_url, session=session)
                if merged_try.get("durl"):
                    direct_url = merged_try["durl"][0].get("url")
                else:
                    dash_try = await self.ugc_playurl(aid=aid, cid=cid, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session)
                    v = self.pick_best_video(dash_try.get("dash") or {})
                    direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""
        elif vtype == "pgc":
            # 番剧（PGC）
            ep_id = ident["ep_id"]
            info = await self.get_pgc_info_by_ep(ep_id, session)
            probe = await self.pgc_playurl_v2(ep_id, qn=120, fnval=FNVAL_MAX, referer=page_url, session=session)
            target_qn = self.best_qn_from_data(probe) or probe.get("quality") or 80

            merged_try = await self.pgc_playurl_v2(ep_id, qn=target_qn, fnval=0, referer=page_url, session=session)
            if merged_try.get("durl"):
                direct_url = merged_try["durl"][0].get("url")
            else:
                dash_try = await self.pgc_playurl_v2(ep_id, qn=target_qn, fnval=FNVAL_MAX, referer=page_url, session=session)
                v = self.pick_best_video(dash_try.get("dash") or {})
                direct_url = (v.get("baseUrl") or v.get("base_url")) if v else ""
        else:
            return None

        if not direct_url:
            return None

        # 检查视频大小
        if not await self.check_video_size(direct_url, session):
            return None  # 视频过大，跳过

        return {
            "video_url": page_url,
            "author": info["author"],
            "title": info["title"],
            "desc": info["desc"],
            "direct_url": direct_url
        }


