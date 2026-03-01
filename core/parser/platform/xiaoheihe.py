import asyncio
import html as html_lib
import json
import re
from typing import Optional, Dict, Any, List, Tuple, Iterable
from urllib.parse import urlparse, parse_qs

import aiohttp

try:
    from astrbot.api import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)

from .base import BaseVideoParser
from ..utils import build_request_headers
from ...constants import Config


UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class XiaoheiheParser(BaseVideoParser):

    def __init__(
        self,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        """初始化解析器并设置并发限制与默认请求头。

        Args:
            use_video_proxy: 视频下载是否使用代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
        """
        super().__init__("xiaoheihe")
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self._default_headers = {
            "User-Agent": UA,
            "Referer": "https://www.xiaoheihe.cn/",
            "Accept-Language": "zh-CN,zh;q=0.9"
        }
    
    def _add_m3u8_prefix_to_urls(self, urls: List[str]) -> List[str]:
        """为 m3u8 URL 列表添加 m3u8: 前缀
        
        Args:
            urls: URL 列表
            
        Returns:
            添加了 m3u8: 前缀的 URL 列表（仅对 m3u8 URL 添加）
        """
        if not urls:
            return urls
        
        result = []
        for url in urls:
            if url and isinstance(url, str):
                url_lower = url.lower()
                if '.m3u8' in url_lower and not url.startswith('m3u8:'):
                    result.append(f'm3u8:{url}')
                else:
                    result.append(url)
            else:
                result.append(url)
        
        return result

    def can_parse(self, url: str) -> bool:
        """判断是否可以解析该 URL。

        Args:
            url: 待判断的链接。

        Returns:
            若该链接能解析出 appid 与 game_type 则返回 True，否则 False。
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        appid, game_type = self._extract_appid_game_type(url)
        ok = bool(appid and game_type)
        logger.debug(
            f"[{self.name}] can_parse: {'可解析' if ok else '不可解析'} "
            f"appid={appid}, game_type={game_type}, url={url}"
        )
        return ok

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取该解析器可处理的链接。

        Args:
            text: 输入文本（可能包含多个链接）。

        Returns:
            可解析的小黑盒链接列表（已过滤掉无法提取 appid/game_type 的候选）。
        """
        candidates = set()

        app_pattern = r"https?://api\.xiaoheihe\.cn/game/share_game_detail[^\s<>\"'()]+"
        candidates.update(re.findall(app_pattern, text, re.IGNORECASE))

        web_pattern = r"https?://(?:www\.)?xiaoheihe\.cn/[^\s<>\"'()]+"
        candidates.update(re.findall(web_pattern, text, re.IGNORECASE))

        result: List[str] = []
        for u in candidates:
            appid, game_type = self._extract_appid_game_type(u)
            if appid and game_type:
                result.append(u)

        if result:
            logger.debug(
                f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: "
                f"{result[:3]}{'...' if len(result) > 3 else ''}"
            )
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result

    def _extract_appid_game_type(self, url: str) -> Tuple[Optional[int], Optional[str]]:
        """从 URL 中提取 appid 与 game_type。

        Args:
            url: 小黑盒分享链接或网页链接。

        Returns:
            二元组 (appid, game_type)：
            - appid: 成功时为 int，否则为 None
            - game_type: 成功时为字符串（例如 pc），否则为 None
        """
        if not url:
            return None, None
        try:
            u = urlparse(url)
        except Exception:
            return None, None

        host = (u.netloc or "").lower()
        path = u.path or ""

        if "api.xiaoheihe.cn" in host and "/game/share_game_detail" in path:
            qs = parse_qs(u.query or "")
            raw_appid = (qs.get("appid") or [None])[0]
            raw_game_type = (qs.get("game_type") or ["pc"])[0] or "pc"
            try:
                return int(raw_appid), raw_game_type
            except Exception:
                return None, raw_game_type

        if "xiaoheihe.cn" in host:
            m = re.search(r"/app/topic/game/(?P<gt>[^/]+)/(?P<appid>\d+)", path, re.I)
            if m:
                try:
                    return int(m.group("appid")), m.group("gt")
                except Exception:
                    return None, m.group("gt")

        return None, None

    def _canonical_web_url(self, appid: int, game_type: str) -> str:
        """构造规范的小黑盒 Web 详情页链接。

        Args:
            appid: 游戏 appid。
            game_type: 游戏类型（例如 pc）。

        Returns:
            标准化后的网页链接。
        """
        gt = (game_type or "pc").strip().lower()
        return f"https://www.xiaoheihe.cn/app/topic/game/{gt}/{appid}"

    @staticmethod
    def _unique_keep_order(urls: Iterable[str]) -> List[str]:
        """去重并保持原有顺序。

        Args:
            urls: URL 可迭代对象。

        Returns:
            去重后的 URL 列表（保持首次出现顺序）。
        """
        seen = set()
        out: List[str] = []
        for u in urls:
            if not u or not isinstance(u, str):
                continue
            if u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out

    @staticmethod
    def _strip_tags(text: str) -> str:
        """粗略清理 HTML 标签并做一定的换行/空白规范化。

        Args:
            text: 原始 HTML 或混合文本。

        Returns:
            清理后的纯文本。
        """
        if not text:
            return ""
        t = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
        t = re.sub(r"(?is)<style[^>]*>.*?</style>", "", t)
        t = re.sub(r"(?is)<video[^>]*>.*?</video>", "", t)
        t = re.sub(r"(?is)<img[^>]*>", "", t)

        t = re.sub(r"(?i)</p\s*>", "\n\n", t)
        t = re.sub(r"(?i)<p[^>]*>", "", t)
        t = re.sub(r"(?i)</div\s*>", "\n", t)
        t = re.sub(r"(?i)<div[^>]*>", "", t)
        t = re.sub(r"(?i)<li[^>]*>", "\n・", t)
        t = re.sub(r"(?i)</li\s*>", "\n", t)
        t = re.sub(r"(?i)</(ul|ol)\s*>", "\n", t)
        t = re.sub(r"(?i)</h[1-6]\s*>", "\n", t)
        t = re.sub(r"(?i)<h[1-6][^>]*>", "\n", t)

        t = re.sub(r"(?i)<br\s*/?>", "\n", t)
        t = re.sub(r"<[^>]+>", "", t)
        t = html_lib.unescape(t)
        t = t.replace("\r\n", "\n").replace("\r", "\n")
        t = t.replace("\u2028", "\n").replace("\u2029", "\n")
        t = t.replace("・・", "・")
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    async def _fetch_game_introduction_api(
        self,
        steam_appid: int,
        session: aiohttp.ClientSession,
    ) -> Optional[Dict[str, Any]]:
        """调用小黑盒 `game_introduction` 接口获取简介与发行信息。

        Args:
            steam_appid: Steam appid。
            session: aiohttp 会话。

        Returns:
            成功时返回接口 `result` 字段（dict），失败返回 None。
        """
        if not steam_appid:
            return None
        api_url = (
            "https://api.xiaoheihe.cn/game/game_introduction/"
            f"?steam_appid={steam_appid}&return_json=1"
        )
        async with session.get(
            api_url,
            headers={**self._default_headers, "Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
        if not isinstance(data, dict):
            return None
        if data.get("status") != "ok":
            return None
        result = data.get("result")
        return result if isinstance(result, dict) else None

    @staticmethod
    def _format_cn_ymd_to_dotted(text: str) -> str:
        """将中文日期（YYYY年M月D日）或常见分隔日期格式化为 `YYYY.M.D`。

        Args:
            text: 日期文本。

        Returns:
            格式化后的日期字符串；若无法识别则返回原始去空白结果。
        """
        if not text:
            return ""
        s = html_lib.unescape(text).strip()
        s = re.sub(r"\s+", "", s)
        m = re.match(r"^(\d{4})年(\d{1,2})月(\d{1,2})日?$", s)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}.{mo}.{d}"
        m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}.{mo}.{d}"
        return text.strip()

    async def _fetch_html(self, url: str, session: aiohttp.ClientSession) -> str:
        """拉取页面 HTML。

        Args:
            url: 页面链接。
            session: aiohttp 会话。

        Returns:
            HTML 文本。

        Raises:
            RuntimeError: 当请求失败（非 200）时。
        """
        async with session.get(
            url,
            headers=self._default_headers,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"无法获取页面内容，状态码: {response.status}")
            return await response.text()

    def _extract_nuxt_data_payload(self, html: str) -> Optional[list]:
        """从 HTML 中提取 Nuxt 注入的 `__NUXT_DATA__` JSON payload。

        Args:
            html: 页面 HTML。

        Returns:
            解析成功时返回 list payload，否则 None。
        """
        if not html:
            return None
        m = re.search(
            r'<script[^>]+id="__NUXT_DATA__"[^>]*>(.*?)</script>',
            html,
            re.S | re.I,
        )
        if not m:
            return None
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except Exception:
            return None
        return data if isinstance(data, list) else None

    def _devalue_resolve_root(self, payload: list) -> Any:
        """将 Nuxt 的 devalue/索引引用结构还原为普通 Python 对象树。

        Nuxt `__NUXT_DATA__` 中经常用“索引引用”来压缩结构，本函数会：
        - 将 `int` 索引引用解析为对应条目
        - 处理部分包装结构（Reactive/Ref/Readonly 等）
        - 尝试规避循环引用导致的递归死循环

        Args:
            payload: `__NUXT_DATA__` 解析得到的 list。

        Returns:
            还原后的根对象（通常为 dict/list）。
        """
        n = len(payload)
        memo: Dict[int, Any] = {}
        resolving: set[int] = set()

        def resolve(v: Any) -> Any:
            if isinstance(v, int) and 0 <= v < n:
                return resolve_idx(v)
            if isinstance(v, list):
                if (
                    len(v) == 2
                    and isinstance(v[0], str)
                    and v[0] in {
                        "ShallowReactive",
                        "Reactive",
                        "Ref",
                        "ShallowRef",
                        "Readonly",
                        "ShallowReadonly",
                    }
                ):
                    return resolve(v[1])
                return [resolve(x) for x in v]
            if isinstance(v, dict):
                return {k: resolve(val) for k, val in v.items()}
            return v

        def resolve_idx(idx: int) -> Any:
            if idx in memo:
                return memo[idx]
            if idx in resolving:
                return None
            resolving.add(idx)
            memo[idx] = None
            memo[idx] = resolve(payload[idx])
            resolving.remove(idx)
            return memo[idx]

        return resolve(0)

    @staticmethod
    def _find_best_game_dict(root: Any, appid: int) -> Optional[Dict[str, Any]]:
        """在还原后的对象树中寻找最“像游戏详情”的 dict。

        Args:
            root: `_devalue_resolve_root` 的返回值。
            appid: 目标 appid（steam_appid/appid 匹配）。

        Returns:
            匹配到的游戏详情 dict；若未找到返回 None。
        """
        if not appid:
            return None
        best: Optional[Dict[str, Any]] = None
        best_score = -1
        stack = [root]
        while stack:
            cur = stack.pop()
            if isinstance(cur, dict):
                if cur.get("appid") == appid or cur.get("steam_appid") == appid:
                    score = 0
                    for k in (
                        "about_the_game",
                        "name",
                        "name_en",
                        "price",
                        "heybox_price",
                        "user_num",
                        "game_award",
                    ):
                        if k in cur:
                            score += 3
                    if "comment_stats" in cur:
                        score += 2
                    if cur.get("steam_appid") == appid:
                        score += 2
                    if score > best_score:
                        best = cur
                        best_score = score
                for v in cur.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(cur, list):
                for v in cur:
                    if isinstance(v, (dict, list)):
                        stack.append(v)
        return best

    @staticmethod
    def _format_people_count(count: Optional[int]) -> str:
        """将评价人数格式化为更易读的中文文本。"""
        if not isinstance(count, int) or count <= 0:
            return ""
        if count >= 10000:
            return f"{count / 10000:.1f} 万人评价"
        return f"{count} 人评价"

    @staticmethod
    def _format_yuan_from_coin(coin: Any) -> str:
        """将小黑盒 coin（千分之一元）转换为人民币字符串。"""
        try:
            c = int(coin)
        except Exception:
            return ""
        value = c / 1000.0
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        return f"{value:.2f}"

    @staticmethod
    def _normalize_value_text(text: str) -> str:
        """规范化展示文本（百分号、小时、货币符号与空白）。"""
        if not text:
            return ""
        v = str(text).strip()
        v = re.sub(r"(\d)\%", r"\1 %", v)
        v = re.sub(r"(\d)h\b", r"\1 h", v, flags=re.I)
        v = re.sub(r"#(\d)", r"# \1", v)
        v = v.replace("￥", "¥ ")
        v = re.sub(r"\s{2,}", " ", v).strip()
        return v

    @staticmethod
    def _extract_rich_text(it: Any) -> str:
        """从 `hb_rich_text.attrs[].text` 中提取拼接后的纯文本。"""
        if not isinstance(it, dict):
            return ""
        rt = it.get("hb_rich_text")
        if not isinstance(rt, dict):
            return ""
        attrs = rt.get("attrs")
        if not isinstance(attrs, list):
            return ""
        parts: List[str] = []
        for a in attrs:
            if isinstance(a, dict) and isinstance(a.get("text"), str):
                parts.append(a["text"])
        return "".join(parts).strip()

    @staticmethod
    def _clean_award_text(text: str) -> str:
        """清理奖项文本中的括号补充说明与多余空白。"""
        if not text:
            return ""
        t = str(text).strip()
        t = re.sub(r"（[^）]*）", "", t)
        t = re.sub(r"\([^)]*\)", "", t)
        return re.sub(r"\s{2,}", " ", t).strip()

    def _format_intro_text(self, text: str) -> str:
        """将简介 HTML/文本清理为更适合消息展示的段落文本。

        Args:
            text: 简介内容（可能包含 HTML）。

        Returns:
            清理后的简介文本。
        """
        if not text:
            return ""
        t = self._strip_tags(text)
        t = t.replace("\u3000", " ").replace("\xa0", " ")
        if "\n" in t:
            t = re.sub(r"[ \t]+\n", "\n", t)
            t = re.sub(r"\n[ \t]+", "\n", t)
            t = re.sub(r"\n{3,}", "\n\n", t).strip()
            return t
        t = re.sub(r"([。！？])\s+(?=[\u4e00-\u9fffA-Za-z0-9])", r"\1\n\n", t)
        t = re.sub(r"。(?=(探索|复仇雪耻))", "。\n\n", t)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()
        return t

    def _parse_types_from_html(self, html: str) -> str:
        """从页面 HTML 中解析“类型/标签”文本。

        Args:
            html: 页面 HTML。

        Returns:
            拼接后的类型文本（可能为空字符串）。
        """
        group1 = ""
        group2_tags: List[str] = []

        m = re.search(r'<div class="row-2">.*?<div class="tags">(.*?)</div></div>', html, re.S | re.I)
        tags_html = m.group(1) if m else ""
        if tags_html:
            m2 = re.search(r'<div class="tag common"[^>]*>(.*?)</div>', tags_html, re.S | re.I)
            if m2:
                spans = re.findall(r"<span[^>]*>(.*?)</span>", m2.group(1), re.S | re.I)
                toks = [self._strip_tags(x) for x in spans]
                toks = [re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]+", "", t) for t in toks]
                toks = [t for t in toks if t]
                if toks:
                    group1 = " ".join(toks)

            raw_tags = re.findall(r'<p class="tag"[^>]*>(.*?)</p>', tags_html, re.S | re.I)
            group2_tags = [self._strip_tags(t) for t in raw_tags]
            group2_tags = [t for t in group2_tags if t]

        parts: List[str] = []
        if group1:
            parts.append(f"[ {group1} ]")
        if group2_tags:
            parts.append(f"[ {' '.join(group2_tags)} ]")
        return " ".join(parts).strip()

    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析小黑盒链接并返回统一结构的结果字典。

        解析流程概览：
        - 从输入 URL 提取 appid/game_type 并规范化为 Web 详情页
        - 拉取 HTML：提取 m3u8 与图片直链
        - 解析 `__NUXT_DATA__`：提取评分/价格/奖项/统计信息
        - 调用 `game_introduction`：补全简介、发行时间、厂商信息

        Args:
            session: aiohttp 会话。
            url: 小黑盒分享链接或 Web 链接。

        Returns:
            解析成功时返回结果字典；解析失败会抛出异常（通常不返回 None）。

        Raises:
            RuntimeError: 当无法提取必要字段或未解析到有效媒体内容时。
        """
        logger.debug(f"[{self.name}] parse: 开始解析 {url}")
        async with self.semaphore:
            appid, game_type = self._extract_appid_game_type(url)
            if not appid or not game_type:
                raise RuntimeError(f"无法从URL提取 appid/game_type: {url}")

            web_url = self._canonical_web_url(appid, game_type)

            logger.debug(f"[{self.name}] parse: 使用Web链接 {web_url}")

            html = await self._fetch_html(web_url, session)

            videos = self._unique_keep_order(re.findall(
                r"https?://[^\"'\s<>]+\.m3u8(?:\?[^\"'\s<>]*)?",
                html, re.I
            ))
            all_images = re.findall(
                r"https?://[^\"'\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'\s<>]*)?",
                html, re.I
            )
            images: List[str] = []
            for img in self._unique_keep_order(all_images):
                img_lower = img.lower()
                if "/thumbnail/" in img_lower:
                    continue
                if any(kw in img_lower for kw in ["gameimg", "steam_item_assets", "screenshot", "game"]):
                    images.append(img)

            types = self._parse_types_from_html(html)

            payload = self._extract_nuxt_data_payload(html)
            if not payload:
                raise RuntimeError("未找到 __NUXT_DATA__，无法解析统计/价格/奖项")
            root = self._devalue_resolve_root(payload)
            game = self._find_best_game_dict(root, appid)
            if not game:
                raise RuntimeError("未找到游戏详情数据（Nuxt 解析失败）")

            name = game.get("name") if isinstance(game.get("name"), str) else ""
            name_en = game.get("name_en") if isinstance(game.get("name_en"), str) else ""
            title = f"{name}（{name_en}）" if (name and name_en) else (name or name_en)
            if not title:
                raise RuntimeError("未解析到游戏标题")

            score = str(game.get("score")).strip() if isinstance(game.get("score"), str) else ""
            score_count = ""
            comment_stats = game.get("comment_stats") if isinstance(game.get("comment_stats"), dict) else {}
            score_comment = comment_stats.get("score_comment")
            if isinstance(score_comment, int):
                score_count = self._format_people_count(score_comment)
            rating_line = ""
            if score:
                rating_line = f"小黑盒评分：{score}"
                if score_count:
                    rating_line = f"小黑盒评分：{score}（{score_count}）"

            steam_appid = game.get("steam_appid")
            if isinstance(steam_appid, str) and steam_appid.isdigit():
                steam_appid = int(steam_appid)
            if not isinstance(steam_appid, int) or steam_appid <= 0:
                steam_appid = appid

            intro_api = await self._fetch_game_introduction_api(steam_appid, session)
            if not intro_api or not isinstance(intro_api.get("about_the_game"), str):
                raise RuntimeError("未获取到简介（game_introduction 接口失败）")

            intro = self._format_intro_text(intro_api.get("about_the_game"))
            release_date = self._format_cn_ymd_to_dotted(str(intro_api.get("release_date") or "").strip())
            developers = intro_api.get("developers")
            publishers = intro_api.get("publishers")
            developer = ""
            publisher = ""
            if isinstance(developers, list):
                vals = []
                for d in developers:
                    if isinstance(d, dict) and isinstance(d.get("value"), str) and d.get("value"):
                        vals.append(d.get("value"))
                developer = ",".join(vals)
            if isinstance(publishers, list):
                vals = []
                for p in publishers:
                    if isinstance(p, dict) and isinstance(p.get("value"), str) and p.get("value"):
                        vals.append(p.get("value"))
                publisher = ",".join(vals)

            stats_map: Dict[str, Dict[str, Any]] = {}
            if isinstance(game.get("user_num"), dict):
                gd = game["user_num"].get("game_data")
                if isinstance(gd, list):
                    for it in gd:
                        if isinstance(it, dict) and isinstance(it.get("desc"), str):
                            stats_map[it["desc"]] = it

            def stat_line(desc_key: str, out_label: str, include_rank: bool = False) -> str:
                it = stats_map.get(desc_key)
                if not it:
                    return ""
                raw = self._extract_rich_text(it) or it.get("value")
                v = self._normalize_value_text(raw)
                if not v:
                    return ""
                if include_rank:
                    rk = it.get("rank")
                    if isinstance(rk, str) and rk.strip():
                        rks = self._normalize_value_text(rk)
                        if rks.startswith("#"):
                            v = f"{v}（{rks}）"
                return f"{out_label}：{v}"

            good_rate_line = stat_line("全语言好评率", "全语言好评率")
            avg_time_line = stat_line("平均游戏时间", "平均游戏时间", include_rank=True)
            online_now_line = stat_line("当前在线", "当前在线")
            yesterday_peak_line = stat_line("昨日峰值在线", "昨日峰值在线", include_rank=True)
            sale_rank_line = stat_line("全球销量排行", "全球销量排行")
            month_avg_line = stat_line("本月平均在线", "本月平均在线", include_rank=True)

            price_line = ""
            current_price_line = ""
            lowest_price_line = ""
            if isinstance(game.get("price"), dict):
                p = game["price"]
                initial = p.get("initial") or p.get("current")
                if initial:
                    price_line = f"价格：¥ {self._normalize_value_text(initial).replace('¥ ', '').replace('¥', '').strip()}"
                lp = p.get("lowest_price")
                if lp:
                    lowest_price_line = (
                        f"史低价格：¥ {self._normalize_value_text(lp).replace('¥ ', '').replace('¥', '').strip()}"
                    )
            if isinstance(game.get("heybox_price"), dict):
                hp = game["heybox_price"]
                cost_coin = hp.get("cost_coin")
                if cost_coin is not None:
                    yuan = self._format_yuan_from_coin(cost_coin)
                    if yuan:
                        current_price_line = f"当前价格：¥ {yuan}"

            lp_it = stats_map.get("史低价格")
            if lp_it:
                v = self._normalize_value_text(lp_it.get("value"))
                if v:
                    v = v.replace("¥", "").strip()
                    lowest_price_line = f"史低价格：¥ {v}"

            awards: List[str] = []
            if isinstance(game.get("game_award"), list):
                for it in game["game_award"]:
                    if isinstance(it, dict):
                        desc = self._clean_award_text(it.get("desc"))
                        detail = self._clean_award_text(it.get("detail_name"))
                        if isinstance(desc, str) and isinstance(detail, str) and desc and detail:
                            awards.append(f"{desc}：{detail}")
            awards = self._unique_keep_order(awards)

            desc_lines: List[str] = []
            desc_lines.append("")
            desc_lines.append("")
            desc_lines.append("=============")
            if intro:
                desc_lines.append(intro)
            desc_lines.append("=============")
            desc_lines.append("")

            if types:
                desc_lines.append(f"类型：{types}")
            if release_date:
                desc_lines.append(f"发布时间：{release_date}")
            if developer:
                desc_lines.append(f"开发商：{developer}")
            if publisher:
                desc_lines.append(f"发行商：{publisher}")
            if rating_line:
                desc_lines.append(rating_line)
            if good_rate_line:
                desc_lines.append(good_rate_line)
            if avg_time_line:
                desc_lines.append(avg_time_line)
            if online_now_line:
                desc_lines.append(online_now_line)
            if yesterday_peak_line:
                desc_lines.append(yesterday_peak_line)

            if sale_rank_line:
                if month_avg_line:
                    desc_lines.append(f"{sale_rank_line}（注意：部分游戏在这里是：{month_avg_line}）")
                else:
                    desc_lines.append(sale_rank_line)
            elif month_avg_line:
                desc_lines.append(month_avg_line)

            if price_line:
                desc_lines.append(price_line)
            if current_price_line:
                desc_lines.append(current_price_line)
            if lowest_price_line:
                desc_lines.append(lowest_price_line)

            if awards:
                desc_lines.append("奖项：")
                for a in awards:
                    desc_lines.append(f"   {a}")

            desc = "\n".join(desc_lines).rstrip()

            prefixed_videos = self._add_m3u8_prefix_to_urls(videos) if videos else []
            video_urls = [[v] for v in prefixed_videos] if prefixed_videos else []
            image_urls = [[img] for img in images] if images else []

            if not video_urls and not image_urls:
                logger.debug(f"[{self.name}] parse: 未找到任何内容 {url}")
                raise RuntimeError(f"未找到任何内容: {url}")

            referer = "https://store.steampowered.com/"
            image_headers = build_request_headers(is_video=False, referer=referer)
            video_headers = build_request_headers(is_video=True, referer=referer)

            result_dict = {
                "url": web_url,
                "source_url": url,
                "title": title or "",
                "author": "",
                "desc": desc,
                "timestamp": release_date or "",
                "video_urls": video_urls,
                "image_urls": image_urls,
                "image_headers": image_headers,
                "video_headers": video_headers,
                "use_video_proxy": self.use_video_proxy,
                "proxy_url": self.proxy_url if self.use_video_proxy else None,
            }
            if video_urls:
                result_dict["video_force_download"] = True
            logger.debug(
                f"[{self.name}] parse: 解析完成 {url}, "
                f"title_len={len(result_dict.get('title') or '')}, "
                f"desc_len={len(result_dict.get('desc') or '')}, "
                f"video_count={len(video_urls)}, image_count={len(image_urls)}"
            )
            return result_dict
