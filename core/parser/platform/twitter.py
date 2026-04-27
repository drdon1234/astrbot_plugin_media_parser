"""Twitter/X 解析器实现。"""
import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, Any, List

import aiohttp

from ...logger import logger

from .base import BaseVideoParser
from ..utils import build_request_headers
from ...constants import Config


class FxTwitterServiceUnavailableError(RuntimeError):
    """FxTwitter 服务不可达、超时或服务端错误。"""


class FxTwitterTweetUnavailableError(RuntimeError):
    """FxTwitter 可访问，但目标推文不可用或响应不是目标内容。"""


def json_dumps_compact(value: Any) -> str:
    """生成无多余空白的 JSON 查询参数。"""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class TwitterParser(BaseVideoParser):

    """Twitter/X 解析器实现。"""
    def __init__(
        self,
        use_parse_proxy: bool = False,
        use_image_proxy: bool = False,
        use_video_proxy: bool = False,
        proxy_url: str = None
    ):
        """初始化Twitter解析器

        Args:
            use_parse_proxy: 解析时是否使用代理
            use_image_proxy: 图片下载是否使用代理
            use_video_proxy: 视频下载是否使用代理
            proxy_url: 代理地址（格式：http://host:port 或 socks5://host:port）
        """
        super().__init__("twitter")
        self.use_parse_proxy = use_parse_proxy
        self.use_image_proxy = use_image_proxy
        self.use_video_proxy = use_video_proxy
        self.proxy_url = proxy_url
        self.semaphore = asyncio.Semaphore(Config.PARSER_MAX_CONCURRENT)
        self.headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate',
        }
    
    def can_parse(self, url: str) -> bool:
        """判断是否可以解析此URL

        Args:
            url: 视频链接

        Returns:
            是否可以解析
        """
        if not url:
            logger.debug(f"[{self.name}] can_parse: URL为空")
            return False
        url_lower = url.lower()
        if 'twitter.com' in url_lower or 'x.com' in url_lower:
            if re.search(r'/status/(\d+)', url):
                logger.debug(f"[{self.name}] can_parse: 匹配Twitter链接 {url}")
                return True
        logger.debug(f"[{self.name}] can_parse: 无法解析 {url}")
        return False

    def extract_links(self, text: str) -> List[str]:
        """从文本中提取Twitter链接

        Args:
            text: 输入文本

        Returns:
            Twitter链接列表
        """
        result_links_set = set()
        seen_ids = set()
        pattern = (
            r'https?://(?:twitter\.com|x\.com)/'
            r'[^\s]*?status/(\d+)[^\s<>"\'()]*'
        )
        matches = re.finditer(pattern, text, re.IGNORECASE)
        for match in matches:
            tweet_id = match.group(1)
            if tweet_id not in seen_ids:
                seen_ids.add(tweet_id)
                result_links_set.add(match.group(0))
        result = list(result_links_set)
        if result:
            logger.debug(f"[{self.name}] extract_links: 提取到 {len(result)} 个链接: {result[:3]}{'...' if len(result) > 3 else ''}")
        else:
            logger.debug(f"[{self.name}] extract_links: 未提取到链接")
        return result


    def _parse_fxtwitter_response(
        self,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """从 FxTwitter 响应中提取统一媒体结构。"""
        if not isinstance(data, dict) or 'tweet' not in data:
            raise FxTwitterTweetUnavailableError("FxTwitter响应缺少tweet字段")

        tweet = data.get('tweet') or {}
        tweet_text = self._twitter_text(tweet)
        author_info = tweet.get('author', {})
        author = self._fxtwitter_author(author_info)
        timestamp = self._parse_twitter_date(tweet.get('created_at'))
        quote = self._extract_fxtwitter_quote(tweet.get('quote'))
        desc = self._build_tweet_desc(tweet_text, quote)

        media_urls = {
            'images': [],
            'videos': [],
            'title': f"{author} 的推文" if author else "Twitter 推文",
            'text': desc,
            'author': self._combine_parenthetical(
                author,
                quote.get("author", "")
            ),
            'timestamp': self._combine_parenthetical(
                timestamp,
                quote.get("timestamp", "")
            ),
        }

        media = tweet.get('media') or {}
        for photo in media.get('photos') or []:
            if isinstance(photo, dict) and photo.get('url'):
                media_urls['images'].append(photo.get('url'))
        for video in media.get('videos') or []:
            if isinstance(video, dict) and video.get('url'):
                media_urls['videos'].append({
                    'url': video.get('url', ''),
                    'thumbnail': video.get('thumbnail_url', ''),
                    'duration': video.get('duration', 0)
                })
        return media_urls

    @staticmethod
    def _twitter_text(tweet: Dict[str, Any]) -> str:
        """提取推文文本，优先使用 raw_text。"""
        if not isinstance(tweet, dict):
            return ""
        raw_text = tweet.get('raw_text')
        if isinstance(raw_text, dict):
            text = raw_text.get('text')
            if text:
                return TwitterParser._apply_display_text_range(
                    str(text),
                    raw_text.get('display_text_range')
                )
        return str(tweet.get('text', '') or '')

    @staticmethod
    def _fxtwitter_author(author_info: Dict[str, Any]) -> str:
        """格式化 FxTwitter 作者信息。"""
        if not isinstance(author_info, dict):
            return ""
        author_name = author_info.get('name', '')
        author_username = author_info.get('screen_name', '')
        if author_name and author_username:
            return f"{author_name}(@{author_username})"
        return author_name or author_username

    @staticmethod
    def _apply_display_text_range(text: str, display_range: Any) -> str:
        """按 Twitter display_text_range 裁剪正文，去掉回复前缀等非正文内容。"""
        if not text or not isinstance(display_range, list) or len(display_range) != 2:
            return text
        try:
            start = max(0, int(display_range[0]))
            end = max(start, int(display_range[1]))
            return text[start:end].strip()
        except (TypeError, ValueError):
            return text

    @staticmethod
    def _parse_twitter_date(created_at: Any) -> str:
        """将 Twitter created_at 转为 YYYY-MM-DD。"""
        if not created_at:
            return ""
        try:
            dt = datetime.strptime(str(created_at), '%a %b %d %H:%M:%S %z %Y')
            return dt.strftime('%Y-%m-%d')
        except Exception:
            return str(created_at)

    def _extract_fxtwitter_quote(self, quote: Any) -> Dict[str, str]:
        """提取 FxTwitter 引用推文信息，供既有 metadata 字段合并使用。"""
        if not isinstance(quote, dict):
            return {}
        quote_text = self._twitter_text(quote)
        if not quote_text:
            return {}
        return {
            "text": quote_text,
            "author": self._fxtwitter_author(quote.get('author') or {}),
            "timestamp": self._parse_twitter_date(quote.get('created_at')),
            "reply_to": str(quote.get("replying_to") or "").strip(),
        }

    async def _fetch_fxtwitter_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str,
        max_retries: int = 3,
        retry_delay: float = 1.0
    ) -> Dict[str, Any]:
        """使用FxTwitter API获取推特媒体直链（带重试机制）

        Args:
            session: aiohttp会话
            tweet_id: 推文ID
            max_retries: 最大重试次数，默认3次
            retry_delay: 重试延迟（秒），默认1秒，使用指数退避

        Returns:
            包含images和videos的字典。
        """
        api_url = f"https://api.fxtwitter.com/status/{tweet_id}"
        last_exception = None

        proxy = self.proxy_url if self.use_parse_proxy else None
        for attempt in range(max_retries + 1):
            try:
                async with session.get(
                    api_url,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    proxy=proxy
                ) as response:
                    response.raise_for_status()
                    data = await response.json()
                    return self._parse_fxtwitter_response(data)
            except aiohttp.ClientResponseError as e:
                if e.status < 500:
                    raise FxTwitterTweetUnavailableError(
                        f"HTTP {e.status} {e.message}"
                    ) from e
                last_exception = e
            except (aiohttp.ClientError, asyncio.TimeoutError, aiohttp.ServerTimeoutError) as e:
                last_exception = e
            except FxTwitterTweetUnavailableError:
                raise
            except Exception as e:
                raise FxTwitterTweetUnavailableError(str(e)) from e

            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt)
                await asyncio.sleep(delay)
            else:
                error_msg = str(last_exception) if last_exception else "未知错误"
                raise FxTwitterServiceUnavailableError(
                    f"{error_msg}（已重试{max_retries}次）"
                )

    @staticmethod
    def _best_video_variant(media: Dict[str, Any]) -> Optional[str]:
        """按 bitrate 选择最佳 mp4 变体。"""
        video_info = media.get("video_info") or {}
        variants = video_info.get("variants") or []
        candidates = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            url = variant.get("url") or ""
            if ".mp4" not in url:
                continue
            try:
                bitrate = int(variant.get("bitrate") or 0)
            except (TypeError, ValueError):
                bitrate = 0
            candidates.append((bitrate, url))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    @staticmethod
    def _build_img_url(media: Dict[str, Any]) -> Optional[str]:
        """构造原图 URL。"""
        media_url = media.get("media_url_https") or media.get("media_url")
        if not media_url:
            return None
        if "?" in media_url:
            return f"{media_url}&name=orig"
        return f"{media_url}?name=orig"

    async def _fetch_guest_token(self, session: aiohttp.ClientSession) -> str:
        """获取 Twitter guest token。"""
        bearer = (
            "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOjj6tT7UeCs"
            "TnIU3U%3D0owR4rQG2v0nE"
        )
        headers = {
            **self.headers,
            "Authorization": f"Bearer {bearer}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        proxy = self.proxy_url if self.use_parse_proxy else None
        async with session.post(
            "https://api.twitter.com/1.1/guest/activate.json",
            headers=headers,
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        token = str(data.get("guest_token") or "").strip()
        if not token:
            raise RuntimeError("Twitter guest token为空")
        return token

    @staticmethod
    def _walk_dicts(obj: Any):
        """深度遍历 dict/list。"""
        if isinstance(obj, dict):
            yield obj
            for value in obj.values():
                yield from TwitterParser._walk_dicts(value)
        elif isinstance(obj, list):
            for value in obj:
                yield from TwitterParser._walk_dicts(value)

    def _parse_graphql_response(
        self,
        data: Dict[str, Any],
        tweet_id: str
    ) -> Dict[str, Any]:
        """从 Guest GraphQL 响应中提取媒体。"""
        tweet = None
        for candidate in self._walk_dicts(data):
            legacy = candidate.get("legacy")
            if not isinstance(legacy, dict):
                continue
            rest_id = str(candidate.get("rest_id") or legacy.get("id_str") or "")
            if rest_id == tweet_id or legacy.get("full_text"):
                tweet = candidate
                if rest_id == tweet_id:
                    break
        if not tweet:
            raise RuntimeError("Twitter GraphQL响应中未找到tweet")

        legacy = tweet.get("legacy") or {}
        author = self._graphql_author(tweet)

        timestamp = ""
        created_at = legacy.get("created_at")
        if created_at:
            try:
                dt = datetime.strptime(created_at, '%a %b %d %H:%M:%S %z %Y')
                timestamp = dt.strftime('%Y-%m-%d')
            except Exception:
                timestamp = str(created_at)

        text = self._graphql_tweet_text(tweet)
        quote = self._extract_graphql_quote(data, legacy)
        desc = self._build_tweet_desc(text, quote)

        images: List[str] = []
        videos: List[Dict[str, Any]] = []
        media_items = ((legacy.get("extended_entities") or {}).get("media") or [])
        for media in media_items:
            if not isinstance(media, dict):
                continue
            media_type = media.get("type")
            if media_type == "photo":
                img_url = self._build_img_url(media)
                if img_url:
                    images.append(img_url)
            elif media_type in ("video", "animated_gif"):
                video_url = self._best_video_variant(media)
                if video_url:
                    videos.append({"url": video_url})

        return {
            "images": images,
            "videos": videos,
            "title": f"{author} 的推文" if author else "Twitter 推文",
            "text": desc,
            "author": self._combine_parenthetical(
                author,
                quote.get("author", "")
            ),
            "timestamp": self._combine_parenthetical(
                timestamp,
                quote.get("timestamp", "")
            ),
        }

    def _graphql_author(self, tweet: Dict[str, Any]) -> str:
        """从 GraphQL tweet 节点提取作者。"""
        user_core = tweet.get("core") or {}
        user_result = (
            ((user_core.get("user_results") or {}).get("result") or {})
            if isinstance(user_core, dict) else {}
        )
        user_legacy = user_result.get("legacy") or {}
        name = user_legacy.get("name") or ""
        screen_name = user_legacy.get("screen_name") or ""
        return f"{name}(@{screen_name})" if name and screen_name else (name or screen_name)

    @staticmethod
    def _graphql_tweet_text(tweet: Dict[str, Any]) -> str:
        """从 GraphQL tweet 节点提取完整文本。"""
        legacy = tweet.get("legacy") or {}
        note_tweet = (
            ((tweet.get("note_tweet") or {}).get("note_tweet_results") or {})
            .get("result") or {}
        )
        if isinstance(note_tweet, dict) and note_tweet.get("text"):
            return str(note_tweet.get("text") or "")
        text = str(legacy.get("full_text") or "")
        return TwitterParser._apply_display_text_range(
            text,
            legacy.get("display_text_range")
        )

    def _extract_graphql_quote(
        self,
        data: Dict[str, Any],
        legacy: Dict[str, Any]
    ) -> Dict[str, str]:
        """从 GraphQL 响应中提取引用推文信息。"""
        quote_id = str(
            legacy.get("quoted_status_id_str") or
            legacy.get("quoted_status_id") or
            ""
        )
        if not quote_id:
            return {}
        for candidate in self._walk_dicts(data):
            candidate_legacy = candidate.get("legacy")
            if not isinstance(candidate_legacy, dict):
                continue
            rest_id = str(
                candidate.get("rest_id") or
                candidate_legacy.get("id_str") or
                ""
            )
            if rest_id != quote_id:
                continue
            quote_text = self._graphql_tweet_text(candidate)
            if not quote_text:
                return {}
            return {
                "text": quote_text,
                "author": self._graphql_author(candidate),
                "timestamp": self._parse_twitter_date(
                    candidate_legacy.get("created_at")
                ),
                "reply_to": str(
                    candidate_legacy.get("in_reply_to_screen_name") or ""
                ).strip(),
            }
        return {}

    @staticmethod
    def _combine_parenthetical(primary: str, secondary: str) -> str:
        """按 B 站转发动态风格合并主/被引用字段。"""
        primary = str(primary or "").strip()
        secondary = str(secondary or "").strip()
        if primary and secondary:
            return f"{primary} ({secondary})"
        return primary or secondary

    @staticmethod
    def _build_tweet_desc(text: str, quote: Dict[str, str]) -> str:
        """将主推文和引用推文合并到 desc，避免新增展示字段。"""
        desc = str(text or "").strip()
        if not isinstance(quote, dict) or not quote.get("text"):
            return desc

        quote_parts = ["引用推文："]
        quote_author = str(quote.get("author") or "").strip()
        quote_reply_to = str(quote.get("reply_to") or "").strip()
        quote_text = str(quote.get("text") or "").strip()
        if quote_author:
            quote_parts.append(quote_author)
        if quote_reply_to:
            quote_parts.append(f"回复 @{quote_reply_to}")
        quote_parts.append(quote_text)

        quote_desc = "\n".join(quote_parts)
        if desc:
            return f"{desc}\n\n{quote_desc}"
        return quote_desc

    async def _fetch_graphql_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str
    ) -> Dict[str, Any]:
        """使用 Twitter Guest GraphQL 回退解析。"""
        bearer = (
            "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOjj6tT7UeCs"
            "TnIU3U%3D0owR4rQG2v0nE"
        )
        guest_token = await self._fetch_guest_token(session)
        variables = {
            "tweetId": tweet_id,
            "withCommunity": False,
            "includePromotedContent": False,
            "withVoice": False,
        }
        features = {
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "tweetypie_unmention_optimization_enabled": True,
            "responsive_web_edit_tweet_api_enabled": True,
            "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
            "view_counts_everywhere_api_enabled": True,
            "longform_notetweets_consumption_enabled": True,
            "responsive_web_twitter_article_tweet_consumption_enabled": True,
            "tweet_awards_web_tipping_enabled": False,
            "freedom_of_speech_not_reach_fetch_enabled": True,
            "standardized_nudges_misinfo": True,
            "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
            "rweb_video_timestamps_enabled": True,
            "longform_notetweets_rich_text_read_enabled": True,
            "longform_notetweets_inline_media_enabled": True,
            "responsive_web_graphql_exclude_directive_enabled": True,
            "verified_phone_label_enabled": False,
            "responsive_web_media_download_video_enabled": False,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
        }
        endpoint = (
            "https://twitter.com/i/api/graphql/"
            "0hWvDhmW8YQ-S_ib3azIrw/TweetResultByRestId"
        )
        headers = {
            **self.headers,
            "Authorization": f"Bearer {bearer}",
            "x-guest-token": guest_token,
            "x-twitter-active-user": "yes",
            "x-twitter-client-language": "en",
            "Referer": "https://twitter.com/",
        }
        proxy = self.proxy_url if self.use_parse_proxy else None
        async with session.get(
            endpoint,
            headers=headers,
            params={
                "variables": json_dumps_compact(variables),
                "features": json_dumps_compact(features),
            },
            proxy=proxy,
            timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            data = await response.json(content_type=None)
        return self._parse_graphql_response(data, tweet_id)

    async def _fetch_media_info(
        self,
        session: aiohttp.ClientSession,
        tweet_id: str
    ) -> Dict[str, Any]:
        """优先 FxTwitter；仅服务不可达/服务端错误时回退 Guest GraphQL。"""
        try:
            return await self._fetch_fxtwitter_info(session, tweet_id)
        except FxTwitterServiceUnavailableError as e:
            logger.warning(f"FxTwitter不可用，尝试GraphQL回退: {e}")
            return await self._fetch_graphql_info(session, tweet_id)


    async def parse(
        self,
        session: aiohttp.ClientSession,
        url: str
    ) -> Optional[Dict[str, Any]]:
        """解析单个Twitter链接

        Args:
            session: aiohttp会话
            url: Twitter链接

        Returns:
            解析结果字典，包含标准化的元数据格式

        Raises:
            RuntimeError: 当解析失败时
        """
        async with self.semaphore:
            tweet_id_match = re.search(r'/status/(\d+)', url)
            if not tweet_id_match:
                raise RuntimeError(f"无法解析此URL: {url}")
            tweet_id = tweet_id_match.group(1)
            media_info = await self._fetch_media_info(session, tweet_id)
            
            images = media_info.get('images', [])
            videos = media_info.get('videos', [])
            text = media_info.get('text', '')
            title = media_info.get('title', '')
            author = media_info.get('author', '')
            timestamp = media_info.get('timestamp', '')
            
            video_urls = []
            image_urls = []
            
            for video_info in videos:
                video_url = video_info.get('url')
                if video_url:
                    video_urls.append(video_url)
            
            image_urls = [img for img in images if img]
            
            has_videos = len(video_urls) > 0
            has_images = len(image_urls) > 0
            has_text = bool(str(text or "").strip())

            if not has_videos and not has_images and not has_text:
                raise RuntimeError("推文不包含文本、图片或视频")
            
            image_headers = build_request_headers(is_video=False)
            video_headers = build_request_headers(is_video=True)
            
            metadata_base = {
                "url": url,
                "title": title or (f"{author} 的推文" if author else "Twitter 推文"),
                "author": author,
                "desc": text,
                "timestamp": timestamp,
                "image_headers": image_headers,
                "video_headers": video_headers,
                "use_image_proxy": self.use_image_proxy,
                "use_video_proxy": self.use_video_proxy,
                "proxy_url": self.proxy_url if (self.use_image_proxy or self.use_video_proxy) else None,
            }
            
            if has_videos and has_images:
                result_dict = {
                    **metadata_base,
                    "video_urls": self._add_range_prefix_to_video_urls([[url] for url in video_urls]),
                    "image_urls": [[url] for url in image_urls],
                    "is_twitter_video": True,
                    "video_force_download": True,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(视频+图片) {url}, video_count={len(video_urls)}, image_count={len(image_urls)}")
                return result_dict
            elif has_videos:
                result_dict = {
                    **metadata_base,
                    "video_urls": self._add_range_prefix_to_video_urls([[url] for url in video_urls]),
                    "image_urls": [],
                    "is_twitter_video": True,
                    "video_force_download": True,
                }
                logger.debug(f"[{self.name}] parse: 解析完成(视频) {url}, video_count={len(video_urls)}")
                return result_dict
            else:
                result_dict = {
                    **metadata_base,
                    "video_urls": [],
                    "image_urls": [[url] for url in image_urls],
                    "is_twitter_video": False,
                }
                if image_urls:
                    logger.debug(f"[{self.name}] parse: 解析完成(图片) {url}, image_count={len(image_urls)}")
                else:
                    logger.debug(f"[{self.name}] parse: 解析完成(纯文本) {url}")
                return result_dict
